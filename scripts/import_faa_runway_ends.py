from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa_runway_ends import (
    ArrestingSystemRow,
    RunwayEndsSourceError,
    discover_apt_csv_url,
    fetch_emas_arresting_system_rows,
)
from app.database import SessionLocal
from app.models import Airport, Installation, Runway

# FAA NASR designations zero-pad single-digit headings ("06/24"); hand-seeded
# rows and most other sources don't ("6/24"). Strip that so the two agree.
_LEADING_ZERO = re.compile(r"^0(\d)")


def _normalize_designation(designation: str) -> str:
    return "/".join(_LEADING_ZERO.sub(r"\1", part) for part in designation.split("/"))


def _group_by_airport(rows: list[ArrestingSystemRow]) -> dict[str, list[ArrestingSystemRow]]:
    by_airport: dict[str, list[ArrestingSystemRow]] = {}
    for row in rows:
        by_airport.setdefault(row.arpt_id, []).append(row)
    return by_airport


def _get_or_create_runway(session: Session, airport: Airport, designation: str) -> Runway:
    normalized = _normalize_designation(designation)
    for runway in airport.runways:
        if _normalize_designation(runway.designation) == normalized:
            return runway
    runway = Runway(airport=airport, designation=normalized)
    session.add(runway)
    session.flush()
    return runway


def _append_note_once(installation: Installation, note: str) -> None:
    """Append note unless it (or an identical prior run's copy) is already there."""
    if note in (installation.notes or ""):
        return
    installation.notes = f"{installation.notes}\n{note}" if installation.notes else note


_OLD_AMBIGUOUS_NOTE_PREFIX = "FAA arresting-system data lists multiple EMAS-equipped ends here:"


def _remove_stale_ambiguous_note(installation: Installation) -> None:
    """Drop a prior run's "genuinely ambiguous" note once same-rwy_id logic resolves it."""
    if not installation.notes:
        return
    kept = [line for line in installation.notes.splitlines() if not line.startswith(_OLD_AMBIGUOUS_NOTE_PREFIX)]
    installation.notes = "\n".join(kept) or None


def enrich_installations(
    session: Session, emas_rows_by_airport: dict[str, list[ArrestingSystemRow]]
) -> dict:
    stats = {
        "enriched": 0,
        "resolved_same_runway_multiple_ends": 0,
        "already_enriched": 0,
        "no_faa_arresting_system_match": 0,
        "ambiguous_multiple_ends": 0,
    }
    installations = session.scalars(
        select(Installation).where(Installation.type.in_(("EMASMAX", "greenEMAS")))
    ).all()

    for installation in installations:
        # runway_id (not runway_end) is the "already handled" marker: the
        # same-runway-multiple-ends case below sets runway_id but leaves
        # runway_end blank, since which specific end still isn't known.
        if installation.runway_id is not None:
            stats["already_enriched"] += 1
            continue

        airport = installation.airport
        candidates = [
            row
            for code in filter(None, (airport.faa_code, airport.iata_code, airport.icao_code))
            for row in emas_rows_by_airport.get(code, [])
        ]
        # A given ARPT_ID could match faa_code and iata_code identically; dedupe.
        seen = set()
        unique_candidates = []
        for row in candidates:
            key = (row.rwy_id, row.rwy_end_id)
            if key not in seen:
                seen.add(key)
                unique_candidates.append(row)

        if not unique_candidates:
            stats["no_faa_arresting_system_match"] += 1
            continue

        if len(unique_candidates) > 1:
            # Candidates naming different ends of one physical runway (e.g.
            # "1/19" ends "1" and "19") aren't actually ambiguous about which
            # runway - only about which end. Link the runway; leave the end
            # unset rather than guessing.
            normalized_rwy_ids = {_normalize_designation(row.rwy_id) for row in unique_candidates}
            if len(normalized_rwy_ids) == 1:
                match = unique_candidates[0]
                runway = _get_or_create_runway(session, airport, match.rwy_id)
                installation.runway = runway
                ends = ", ".join(sorted({row.rwy_end_id for row in unique_candidates}))
                _remove_stale_ambiguous_note(installation)
                _append_note_once(
                    installation,
                    f"FAA arresting-system data lists EMAS at multiple ends of runway "
                    f"{runway.designation} ({ends}); exact end not recorded.",
                )
                stats["resolved_same_runway_multiple_ends"] += 1
                continue

            # Genuinely different runways for one Installation row - no safe
            # link to make; record the raw data as text only.
            stats["ambiguous_multiple_ends"] += 1
            ends = ", ".join(sorted(f"{row.rwy_id}/{row.rwy_end_id}" for row in unique_candidates))
            _append_note_once(
                installation, f"FAA arresting-system data lists multiple EMAS-equipped ends here: {ends}."
            )
            continue

        match = unique_candidates[0]
        runway = _get_or_create_runway(session, airport, match.rwy_id)
        installation.runway = runway
        installation.runway_end = match.rwy_end_id
        stats["enriched"] += 1

    return stats


def run(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    try:
        apt_csv_url = discover_apt_csv_url(client=client, today=today or date.today())
        rows = fetch_emas_arresting_system_rows(client=client, apt_csv_url=apt_csv_url)
        emas_rows_by_airport = _group_by_airport(rows)

        with session_factory() as session:
            stats = enrich_installations(session, emas_rows_by_airport)
            session.commit()
    finally:
        if owns_client:
            client.close()

    stats["faa_emas_rows_fetched"] = len(rows)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enrich Installation rows with runway_id/runway_end using the FAA's "
            "NASR arresting-system data (APT_ARS.csv)."
        )
    )
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args(argv)

    if not args.allow_live_network:
        print("Refusing: --allow-live-network is required.", file=sys.stderr)
        return 2
    if not args.allow_database_write:
        print("Refusing: --allow-database-write is required.", file=sys.stderr)
        return 2

    try:
        stats = run()
    except (RunwayEndsSourceError, httpx.HTTPError) as exc:
        print(f"Enrichment failed: {exc}", file=sys.stderr)
        return 1

    print(f"FAA EMAS arresting-system rows fetched:      {stats['faa_emas_rows_fetched']}")
    print(f"Installations enriched (single end match):   {stats['enriched']}")
    print(f"Resolved (multiple ends, same runway):       {stats['resolved_same_runway_multiple_ends']}")
    print(f"Already enriched (skipped):                  {stats['already_enriched']}")
    print(f"No FAA arresting-system match:               {stats['no_faa_arresting_system_match']}")
    print(f"Still ambiguous (different runways, noted):  {stats['ambiguous_multiple_ends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
