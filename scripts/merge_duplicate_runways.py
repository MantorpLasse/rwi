"""Merge Runway rows that are the same physical runway under different
leading-zero formatting (e.g. seed data's "6/24" vs FAA NASR's "06/24").

scripts/import_faa_runway_ends.py's _get_or_create_runway now normalizes
designations before matching/creating, so duplicates like this can't form
going forward - this is a one-time cleanup for the two rows created before
that fix (Manchester-Boston/MHT and Cape Cod Gateway/HYA, both "06/24" vs
their already-seeded "6/24").

For each airport, groups its Runway rows by normalized designation. Within
a group of more than one, keeps the row with length_m populated (the
original, richer seed row) as canonical - falling back to the lowest id if
neither/both have length_m - repoints every Installation.runway_id and
Signal.runway_id from the others to it, normalizes the canonical row's own
designation, and deletes the others.

Safe to re-run: a second run finds no more duplicate groups.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation, Runway, Signal
from scripts.import_faa_runway_ends import _normalize_designation


def find_duplicate_groups(session: Session) -> list[list[Runway]]:
    by_airport: dict[int, list[Runway]] = defaultdict(list)
    for runway in session.scalars(select(Runway)).all():
        by_airport[runway.airport_id].append(runway)

    groups: list[list[Runway]] = []
    for runways in by_airport.values():
        by_normalized: dict[str, list[Runway]] = defaultdict(list)
        for runway in runways:
            by_normalized[_normalize_designation(runway.designation)].append(runway)
        groups.extend(group for group in by_normalized.values() if len(group) > 1)
    return groups


def _pick_canonical(group: list[Runway]) -> Runway:
    return sorted(group, key=lambda r: (r.length_m is None, r.id))[0]


def merge_duplicates(session: Session) -> dict:
    stats = {"groups_merged": 0, "runways_deleted": 0, "installations_repointed": 0, "signals_repointed": 0}

    for group in find_duplicate_groups(session):
        canonical = _pick_canonical(group)
        canonical.designation = _normalize_designation(canonical.designation)
        stats["groups_merged"] += 1

        for runway in group:
            if runway.id == canonical.id:
                continue
            # Reassign via the relationship attribute, not the raw FK column -
            # SQLAlchemy's unit-of-work nullifies runway_id on any child still
            # in runway.installations/.signals when the parent is deleted,
            # which would silently undo a plain `.runway_id = ...` assignment.
            for installation in list(runway.installations):
                installation.runway = canonical
                stats["installations_repointed"] += 1
            for signal in list(runway.signals):
                signal.runway = canonical
                stats["signals_repointed"] += 1
            session.delete(runway)
            stats["runways_deleted"] += 1

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = merge_duplicates(session)
    print(stats)


if __name__ == "__main__":
    main()
