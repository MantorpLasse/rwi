from __future__ import annotations

import argparse
import re
import sys
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa_construction_report import (
    ConstructionReportError,
    ConstructionReportMatch,
    fetch_current_report_matches,
)
from app.database import SessionLocal
from app.models import Airport, Signal, Source
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE

_RUNWAY_NUMBER = re.compile(r"\b(\d{1,2})\b")
_PHASE_NUMBER = re.compile(r"PHASE\s*(\d+)", re.IGNORECASE)
_REPLACEMENT_WORDS = ("RECONSTRUCT", "REPLACE")


def classify_category(text: str) -> str:
    upper = text.upper()
    if any(word in upper for word in _REPLACEMENT_WORDS):
        return "replacement"
    return "new_installation"


def find_airport(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            (Airport.iata_code == code) | (Airport.icao_code == code) | (Airport.faa_code == code)
        )
    )


def _mentions_keyword(signal: Signal) -> bool:
    haystack = f"{signal.title} {signal.notes or ''}"
    return "EMAS" in haystack or "arresting system" in haystack.lower()


def find_candidate_signals(session: Session, airport: Airport) -> list[Signal]:
    signals = session.scalars(
        select(Signal).where(
            Signal.airport_id == airport.id,
            Signal.category != "replacement_after_incident",
        )
    ).all()
    return [s for s in signals if _mentions_keyword(s)]


def _candidate_score(signal: Signal, match: ConstructionReportMatch) -> int:
    """Higher = more likely this existing Signal is the same real-world project
    as the report match. An airport can have several EMAS-mentioning signals
    from different sources (seed data, AIP grants, USAspending) describing the
    same project under different phase numbers - this breaks the tie instead
    of guessing. See PLAN_FORENKLING.md's "FAA Construction Impact Report"
    section for the worked BOS example this was validated against.
    """
    haystack = f"{signal.title} {signal.notes or ''}".upper()
    score = 0
    if signal.planning_year and match.start_date and signal.planning_year == match.start_date.year:
        score += 2
    report_runways = set(_RUNWAY_NUMBER.findall(match.project_name))
    signal_runways = set(_RUNWAY_NUMBER.findall(haystack))
    if report_runways & signal_runways:
        score += 1
    phase = _PHASE_NUMBER.search(match.project_name)
    if phase and f"PHASE {phase.group(1)}" in haystack:
        score += 1
    return score


def resolve_signal_to_update(session: Session, airport: Airport, match: ConstructionReportMatch) -> Signal | None:
    """Returns the existing Signal to update, or None if a new one should be
    created (no candidates, or an unresolvable tie between equally-plausible
    candidates - safer to create a fresh row than silently update the wrong one)."""
    candidates = find_candidate_signals(session, airport)
    if not candidates:
        return None
    scored = sorted(
        ((_candidate_score(s, match), s) for s in candidates), key=lambda pair: pair[0], reverse=True
    )
    top_score, top_signal = scored[0]
    if len(scored) > 1 and scored[1][0] == top_score:
        return None
    return top_signal


def _confirmation_note(match: ConstructionReportMatch) -> str:
    return (
        f"Confirmed by FAA Construction Impact Report: {match.project_name} "
        f"({match.estimated_dates_raw}, {match.status}). {match.description}"
    )


def apply_match(
    session: Session, airport: Airport, match: ConstructionReportMatch, *, source: Source
) -> tuple[Signal, bool]:
    """Returns (signal, created)."""
    existing = resolve_signal_to_update(session, airport, match)
    if existing is not None:
        existing.status = "under construction"
        existing.construction_start = match.start_date
        existing.completion_date = match.end_date
        note = _confirmation_note(match)
        existing.notes = f"{existing.notes}\n{note}" if existing.notes else note
        return existing, False

    confidence = "high"
    signal = Signal(
        airport=airport,
        source=source,
        title=f"{match.project_name} ({match.airport_code})",
        category=classify_category(f"{match.project_name} {match.description}"),
        confidence=confidence,
        status="under construction",
        probability_score=DEFAULT_SCORE_BY_CONFIDENCE[confidence],
        construction_start=match.start_date,
        completion_date=match.end_date,
        planning_year=match.start_date.year if match.start_date else None,
        notes=f"{match.description} ({match.estimated_dates_raw}, {match.status}).",
    )
    session.add(signal)
    session.flush()
    return signal, True


def import_all(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "matches_found": 0,
        "signals_updated": 0,
        "signals_created": 0,
        "already_imported": 0,
        "airports_not_found": [],
    }
    try:
        url, quarter, year, matches = fetch_current_report_matches(client=client)
        stats["matches_found"] = len(matches)

        with session_factory() as session:
            for match in matches:
                airport = find_airport(session, match.airport_code)
                if airport is None:
                    stats["airports_not_found"].append(match.airport_code)
                    continue

                external_id = (
                    f"faa_construction_report:Q{quarter}_{year}:"
                    f"{match.airport_code}:{match.project_id or match.project_name}"
                )
                if session.scalar(select(Source).where(Source.external_id == external_id)):
                    stats["already_imported"] += 1
                    continue

                source = Source(
                    title=f"FAA Q{quarter} {year} Airport Construction Impact Report: {match.project_name}",
                    source_type="faa_construction_report",
                    publisher="Federal Aviation Administration",
                    url=url,
                    document_reference=f"{match.airport_code}/{match.project_id or '?'}",
                    summary=match.description,
                    reliability_level="official",
                    external_id=external_id,
                )
                session.add(source)
                session.flush()

                _signal, created = apply_match(session, airport, match, source=source)
                if created:
                    stats["signals_created"] += 1
                else:
                    stats["signals_updated"] += 1
                session.commit()
    finally:
        if owns_client:
            client.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the latest FAA Airport Construction Impact Report, find every "
            "EMAS/arresting-system project, and update the matching Signal (or "
            "create a new high-confidence one) with its construction dates."
        )
    )
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args(argv)

    if not args.allow_live_network:
        print("Refusing import: --allow-live-network is required.", file=sys.stderr)
        return 2
    if not args.allow_database_write:
        print("Refusing import: --allow-database-write is required.", file=sys.stderr)
        return 2

    try:
        stats = import_all()
    except (ConstructionReportError, httpx.HTTPError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print(f"EMAS/arresting-system matches found: {stats['matches_found']}")
    print(f"Signals updated:                     {stats['signals_updated']}")
    print(f"Signals created:                     {stats['signals_created']}")
    print(f"Already imported (skipped):          {stats['already_imported']}")
    if stats["airports_not_found"]:
        print(f"Airports not found: {', '.join(stats['airports_not_found'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
