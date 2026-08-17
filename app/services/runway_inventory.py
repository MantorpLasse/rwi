"""Deterministic canonical runway/runway-end inventory planning.

Turns preserved NASR APT_RWY.csv/APT_RWY_END.csv rows (already filtered to
one airport by the caller) into a plan of what canonical Runway/RunwayEnd
rows already exist, would be created, or (for an existing Runway missing
dimensions) would be enriched. Nothing here writes to a session unless
apply_plan() is called explicitly - plan_airport_inventory() and
evaluate_identity_links() only ever read.

This module never touches PhysicalInstallationIdentity.runway_id/
runway_end (the free-text field) and never sets runway_end_id itself -
evaluate_identity_links() only *proposes* matches for separate, explicit,
human-approved application (docs/domain/canonical-runway-runway-end-design.md S7).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from app.services.runway_identity import (
    AmbiguousRunwayDesignationError,
    normalize_end,
    normalize_pair,
    pair_ends,
)

_FEET_TO_METERS = 0.3048


@dataclass(frozen=True)
class RunwayEndPlan:
    designation: str
    raw_designation: str
    existing_id: int | None


@dataclass(frozen=True)
class RunwayPlan:
    normalized_designation: str
    raw_designation: str
    length_m: int | None
    width_m: int | None
    surface: str | None
    existing_id: int | None
    would_enrich: bool
    ends: list[RunwayEndPlan] = field(default_factory=list)


@dataclass(frozen=True)
class IdentityLinkProposal:
    identity_id: int
    current_runway_end: str
    matched_runway_designation: str
    matched_runway_end_designation: str
    target_runway_id: int | None
    target_runway_end_id: int | None


def _feet_to_meters(raw_value: str) -> int | None:
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        feet = float(raw_value)
    except ValueError:
        return None
    return round(feet * _FEET_TO_METERS)


def _existing_runway_ends_by_runway_id(session: Session, runway_ids: list[int]) -> dict[int, dict[str, RunwayEnd]]:
    """Explicit query, not the ORM relationship - so this degrades cleanly
    to "no existing ends" (rather than raising) when runway_ends doesn't
    exist yet, e.g. a real, not-yet-migrated database during a dry run."""
    if not runway_ids:
        return {}
    try:
        rows = session.scalars(select(RunwayEnd).where(RunwayEnd.runway_id.in_(runway_ids))).all()
    except OperationalError:
        session.rollback()
        return {}
    by_runway: dict[int, dict[str, RunwayEnd]] = {}
    for end in rows:
        by_runway.setdefault(end.runway_id, {})[normalize_end(end.designation)] = end
    return by_runway


def plan_airport_inventory(session: Session, airport: Airport, runway_rows, runway_end_rows) -> list[RunwayPlan]:
    """Read-only: only ever SELECTs, regardless of whether runway_ends
    exists yet. runway_rows/runway_end_rows must already be filtered to
    this airport's ARPT_ID by the caller (matching the existing
    scripts/import_faa_runway_ends.py precedent of filtering before, not
    inside, the shared logic)."""
    ends_by_raw_pair: dict[str, list] = {}
    for end_row in runway_end_rows:
        ends_by_raw_pair.setdefault(end_row.values["RWY_ID"], []).append(end_row)

    existing_runways = session.scalars(select(Runway).where(Runway.airport_id == airport.id)).all()
    existing_runways_by_designation = {normalize_pair(runway.designation): runway for runway in existing_runways}
    existing_ends_by_runway_id = _existing_runway_ends_by_runway_id(session, [r.id for r in existing_runways])

    plans: list[RunwayPlan] = []
    for rwy_row in runway_rows:
        raw_pair = rwy_row.values["RWY_ID"]
        normalized_pair = normalize_pair(raw_pair)
        length_m = _feet_to_meters(rwy_row.values["RWY_LEN"])
        width_m = _feet_to_meters(rwy_row.values["RWY_WIDTH"])
        surface = rwy_row.values["SURFACE_TYPE_CODE"].strip() or None

        existing_runway = existing_runways_by_designation.get(normalized_pair)
        would_enrich = existing_runway is not None and (
            existing_runway.length_m is None or existing_runway.width_m is None or existing_runway.surface is None
        )
        existing_ends_by_designation = (
            existing_ends_by_runway_id.get(existing_runway.id, {}) if existing_runway is not None else {}
        )

        end_plans: list[RunwayEndPlan] = []
        for end_row in ends_by_raw_pair.get(raw_pair, []):
            raw_end = end_row.values["RWY_END_ID"]
            normalized_end_designation = normalize_end(raw_end)
            existing_end = existing_ends_by_designation.get(normalized_end_designation)
            end_plans.append(
                RunwayEndPlan(
                    designation=normalized_end_designation,
                    raw_designation=raw_end,
                    existing_id=existing_end.id if existing_end is not None else None,
                )
            )

        expected_ends = set(pair_ends(normalized_pair))
        actual_ends = {end_plan.designation for end_plan in end_plans}
        if actual_ends and actual_ends != expected_ends:
            raise AmbiguousRunwayDesignationError(
                f"runway {raw_pair}: APT_RWY_END.csv ends {sorted(actual_ends)} do not match "
                f"the pair designation's own ends {sorted(expected_ends)}"
            )

        plans.append(
            RunwayPlan(
                normalized_designation=normalized_pair,
                raw_designation=raw_pair,
                length_m=length_m,
                width_m=width_m,
                surface=surface,
                existing_id=existing_runway.id if existing_runway is not None else None,
                would_enrich=would_enrich,
                ends=end_plans,
            )
        )
    return plans


def apply_plan(session: Session, airport: Airport, plans: list[RunwayPlan]) -> dict:
    """Writes canonical Runway/RunwayEnd rows from an already-computed plan.

    Only ever called from isolated-DB tests (and, later, an explicitly
    approved real apply) - never from a dry-run path. Reuses an existing
    Runway row (matched by normalized designation) rather than duplicating
    it, enriching missing length/width/surface only; never overwrites a
    populated value.
    """
    stats = {"runways_created": 0, "runways_enriched": 0, "runway_ends_created": 0}
    for plan in plans:
        if plan.existing_id is not None:
            runway = session.get(Runway, plan.existing_id)
            if plan.would_enrich:
                if runway.length_m is None:
                    runway.length_m = plan.length_m
                if runway.width_m is None:
                    runway.width_m = plan.width_m
                if runway.surface is None:
                    runway.surface = plan.surface
                stats["runways_enriched"] += 1
        else:
            runway = Runway(
                airport=airport,
                designation=plan.normalized_designation,
                length_m=plan.length_m,
                width_m=plan.width_m,
                surface=plan.surface,
            )
            session.add(runway)
            session.flush()
            stats["runways_created"] += 1

        for end_plan in plan.ends:
            if end_plan.existing_id is None:
                session.add(RunwayEnd(runway_id=runway.id, designation=end_plan.designation))
                stats["runway_ends_created"] += 1

    session.flush()
    return stats


def _ends_by_designation(plans: list[RunwayPlan]) -> dict[str, list[tuple[RunwayPlan, RunwayEndPlan]]]:
    ends_by_designation: dict[str, list[tuple[RunwayPlan, RunwayEndPlan]]] = {}
    for plan in plans:
        for end_plan in plan.ends:
            ends_by_designation.setdefault(end_plan.designation, []).append((plan, end_plan))
    return ends_by_designation


def _match_one_end(raw_runway_end: str | None, ends_by_designation: dict) -> tuple[RunwayPlan, RunwayEndPlan] | None:
    """Pure matching rule, shared by the ORM path (evaluate_identity_links,
    used post-migration / in isolated tests) and the raw-row path
    (evaluate_identity_links_from_raw, used for a real, not-yet-migrated
    database where PhysicalInstallationIdentity.runway_end_id doesn't exist
    as a column yet). Zero or ambiguous (>1) matches return None - never
    guessed."""
    if not raw_runway_end:
        return None
    try:
        normalized = normalize_end(raw_runway_end)
    except AmbiguousRunwayDesignationError:
        return None
    matches = ends_by_designation.get(normalized, [])
    if len(matches) != 1:
        return None
    return matches[0]


def evaluate_identity_links(
    session: Session, airport: Airport, plans: list[RunwayPlan]
) -> list[IdentityLinkProposal]:
    """Read-only. Proposes - never applies - a runway_end_id link for each
    existing, still-unlinked PhysicalInstallationIdentity whose free-text
    runway_end normalizes to exactly one planned runway end for this
    airport. Zero or ambiguous (>1) matches are silently excluded, not
    guessed - the caller decides what to do with an identity that has no
    proposal. Requires PhysicalInstallationIdentity.runway_end_id to exist
    as a column (i.e. this slice's migration already applied) - for a real,
    not-yet-migrated database, use evaluate_identity_links_from_raw instead."""
    ends_by_designation = _ends_by_designation(plans)

    identities = session.scalars(
        select(PhysicalInstallationIdentity).where(
            PhysicalInstallationIdentity.airport_id == airport.id,
            PhysicalInstallationIdentity.runway_end_id.is_(None),
        )
    ).all()

    proposals: list[IdentityLinkProposal] = []
    for identity in identities:
        match = _match_one_end(identity.runway_end, ends_by_designation)
        if match is None:
            continue
        plan, end_plan = match
        proposals.append(
            IdentityLinkProposal(
                identity_id=identity.id,
                current_runway_end=identity.runway_end,
                matched_runway_designation=plan.normalized_designation,
                matched_runway_end_designation=end_plan.designation,
                target_runway_id=plan.existing_id,
                target_runway_end_id=end_plan.existing_id,
            )
        )
    return proposals


def evaluate_identity_links_from_raw(
    identity_rows: list[tuple[int, int, int | None, str | None]], plans: list[RunwayPlan]
) -> list[IdentityLinkProposal]:
    """Same matching rule as evaluate_identity_links, for a real database
    that hasn't had this slice's migration applied yet (no
    runway_end_id column to query, so identities are read as plain
    (id, airport_id, runway_id, runway_end) tuples - see
    scripts/dry_run_canonical_runway_inventory.py). Every row passed in is
    treated as unlinked, since runway_end_id cannot exist before migration."""
    ends_by_designation = _ends_by_designation(plans)
    proposals: list[IdentityLinkProposal] = []
    for identity_id, _airport_id, _runway_id, runway_end in identity_rows:
        match = _match_one_end(runway_end, ends_by_designation)
        if match is None:
            continue
        plan, end_plan = match
        proposals.append(
            IdentityLinkProposal(
                identity_id=identity_id,
                current_runway_end=runway_end,
                matched_runway_designation=plan.normalized_designation,
                matched_runway_end_designation=end_plan.designation,
                target_runway_id=plan.existing_id,
                target_runway_end_id=end_plan.existing_id,
            )
        )
    return proposals


# ---------------------------------------------------------------------------
# Clean-batch classification (docs/domain/canonical-runway-us-wide-dry-run-report.md
# S6, docs/domain/canonical-runway-us-clean-batch-report.md).
#
# Report-only: these labels are never persisted and never change how
# plan_airport_inventory()/apply_plan() themselves behave. They only decide
# which airports a *caller* (e.g. an "apply only the deterministic subset"
# script) considers safe to include. Every classification function here
# only ever SELECTs, exactly like plan_airport_inventory().
# ---------------------------------------------------------------------------

ALREADY_COMPLETE = "ALREADY_COMPLETE"
CLEAN_ENRICH = "CLEAN_ENRICH"
CLEAN_CREATE = "CLEAN_CREATE"
PARTIAL_MATCH = "PARTIAL_MATCH"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
UNRESOLVED = "UNRESOLVED"

# Safe to include in a deterministic clean-batch apply.
CLEAN_BATCH_CLASSIFICATIONS = frozenset({ALREADY_COMPLETE, CLEAN_ENRICH, CLEAN_CREATE})


@dataclass(frozen=True)
class AirportBatchClassification:
    airport_id: int
    classification: str
    error: str | None
    existing_runway_count: int
    runways_would_create: int
    runways_would_enrich: int
    runway_ends_would_create: int
    existing_runway_matches: int
    plans: tuple[RunwayPlan, ...] = ()


def classify_airport_batch(
    session: Session, airport: Airport, runway_rows, runway_end_rows
) -> AirportBatchClassification:
    """Read-only classification of one airport's canonical-inventory plan.

    runway_rows/runway_end_rows must already be filtered to this airport
    (same convention as plan_airport_inventory). An empty runway_rows means
    no NASR match was found for this airport's identifier(s) - UNRESOLVED,
    not attempted. A malformed source row (e.g. a helipad or other
    non-two-ended RWY_ID mixed into APT_RWY.csv) surfaces exactly as
    plan_airport_inventory already reports it - AmbiguousRunwayDesignationError
    - classified here as AMBIGUOUS, or CONFLICT when the pair heading and
    APT_RWY_END.csv's actual ends disagree. No new normalization rule is
    applied and no row is silently dropped."""
    existing_runways = session.scalars(select(Runway).where(Runway.airport_id == airport.id)).all()
    existing_count = len(existing_runways)

    if not runway_rows:
        return AirportBatchClassification(
            airport_id=airport.id,
            classification=UNRESOLVED,
            error="no NASR runway rows matched this airport's identifier(s)",
            existing_runway_count=existing_count,
            runways_would_create=0,
            runways_would_enrich=0,
            runway_ends_would_create=0,
            existing_runway_matches=0,
        )

    try:
        plans = plan_airport_inventory(session, airport, runway_rows, runway_end_rows)
    except AmbiguousRunwayDesignationError as exc:
        message = str(exc)
        classification = CONFLICT if "do not match the pair designation" in message else AMBIGUOUS
        return AirportBatchClassification(
            airport_id=airport.id,
            classification=classification,
            error=message,
            existing_runway_count=existing_count,
            runways_would_create=0,
            runways_would_enrich=0,
            runway_ends_would_create=0,
            existing_runway_matches=0,
        )

    creates = sum(1 for p in plans if p.existing_id is None)
    enrich = sum(1 for p in plans if p.existing_id is not None and p.would_enrich)
    end_creates = sum(1 for p in plans for e in p.ends if e.existing_id is None)
    matched_existing_ids = {p.existing_id for p in plans if p.existing_id is not None}
    unmatched_existing = existing_count - len(matched_existing_ids)

    normalized_pairs = [p.normalized_designation for p in plans]
    has_duplicate_pairs = len(normalized_pairs) != len(set(normalized_pairs))

    if has_duplicate_pairs:
        classification = CONFLICT
        error = "two NASR rows for this airport normalize to the same runway pair"
    elif creates == 0 and enrich == 0 and end_creates == 0 and existing_count > 0:
        classification = ALREADY_COMPLETE
        error = None
    elif unmatched_existing > 0:
        classification = PARTIAL_MATCH
        error = f"{unmatched_existing} existing Runway row(s) have no NASR counterpart"
    elif existing_count == 0:
        classification = CLEAN_CREATE
        error = None
    else:
        classification = CLEAN_ENRICH
        error = None

    return AirportBatchClassification(
        airport_id=airport.id,
        classification=classification,
        error=error,
        existing_runway_count=existing_count,
        runways_would_create=creates,
        runways_would_enrich=enrich,
        runway_ends_would_create=end_creates,
        existing_runway_matches=len(matched_existing_ids),
        plans=tuple(plans),
    )


def resolve_us_clean_batch(
    session: Session, all_runway_rows, all_runway_end_rows, *, country: str = "USA"
) -> list[AirportBatchClassification]:
    """Read-only. Classifies every session.country airport (default "USA")
    against the full, unfiltered NASR row sets, deriving airport-to-NASR
    matching the same way the existing pilot scripts already do: an
    airport's candidate codes are {faa_code, iata_code, icao_code} minus
    None, and a NASR row belongs to it when its ARPT_ID is in that set. No
    new matching or normalization rule is introduced. An airport with no
    identifier at all, or whose identifier claims a NASR ARPT_ID also
    claimed by another airport in this database, is never included in the
    clean batch."""
    airports = session.scalars(select(Airport).where(Airport.country == country)).all()

    airport_codes: dict[int, set[str]] = {}
    code_owners: dict[str, set[int]] = {}
    for airport in airports:
        codes = {c for c in (airport.faa_code, airport.iata_code, airport.icao_code) if c}
        airport_codes[airport.id] = codes
        for code in codes:
            code_owners.setdefault(code, set()).add(airport.id)
    collided_codes = {code for code, owners in code_owners.items() if len(owners) > 1}

    results: list[AirportBatchClassification] = []
    for airport in airports:
        candidate_codes = airport_codes[airport.id]
        if not candidate_codes:
            existing_count = len(session.scalars(select(Runway).where(Runway.airport_id == airport.id)).all())
            results.append(
                AirportBatchClassification(
                    airport_id=airport.id,
                    classification=UNRESOLVED,
                    error="no FAA/IATA/ICAO identifier",
                    existing_runway_count=existing_count,
                    runways_would_create=0,
                    runways_would_enrich=0,
                    runway_ends_would_create=0,
                    existing_runway_matches=0,
                )
            )
            continue

        source_rwy = [r for r in all_runway_rows if r.values["ARPT_ID"] in candidate_codes]
        source_end = [r for r in all_runway_end_rows if r.values["ARPT_ID"] in candidate_codes]

        entry = classify_airport_batch(session, airport, source_rwy, source_end)
        if entry.classification != UNRESOLVED and candidate_codes & collided_codes:
            entry = replace(
                entry,
                classification=CONFLICT,
                error="NASR ARPT_ID is also claimed by another airport in this database",
                plans=(),
            )
        results.append(entry)
    return results


def clean_batch_aggregate(classifications: list[AirportBatchClassification]) -> dict:
    """Read-only aggregate summary of a resolve_us_clean_batch() result."""
    clean = [c for c in classifications if c.classification in CLEAN_BATCH_CLASSIFICATIONS]
    return {
        "airports_processed": len(classifications),
        "clean_airport_count": len(clean),
        "excluded_airport_count": len(classifications) - len(clean),
        "runways_would_create": sum(c.runways_would_create for c in clean),
        "runways_would_enrich": sum(c.runways_would_enrich for c in clean),
        "runway_ends_would_create": sum(c.runway_ends_would_create for c in clean),
    }
