from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import Callable

import httpx
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.acquisition.usaspending_grants import (
    UsaspendingError,
    UsaspendingGrant,
    fetch_all_emas_grants,
)
from app.database import SessionLocal, engine
from app.models import Airport, Signal, Source
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE

# "...CARTERSVILLE-BARTOW COUNTY AIRPORT (VPC), LOCATED IN..." - state block
# grants sometimes name a specific airport with its Loc ID this way.
LOC_ID_PATTERN = re.compile(r"AIRPORT\s*\(([A-Z0-9]{3,4})\)")
# "...INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR
# AIRPORTS ASSOCIATED WITH BINGHAMTON, NEW YORK." - present on every
# per-airport grant record (not on state block-grant-administration records).
BENEFICIARY_PATTERN = re.compile(
    r"FEDERAL FUNDING FOR AIRPORTS ASSOCIATED WITH ([A-Z .'\-]+), ([A-Z ]+)\."
)
_REPLACEMENT_WORDS = ("RECONSTRUCT", "REPLACE")


def ensure_source_external_id_column(bind=engine) -> None:
    """Add sources.external_id if an older database doesn't have it yet.

    Base.metadata.create_all() only creates missing tables, not missing
    columns on ones that already exist - idempotent, safe to call every run.
    """
    existing = {c["name"] for c in inspect(bind).get_columns("sources")}
    with bind.begin() as connection:
        if "external_id" not in existing:
            connection.execute(text("ALTER TABLE sources ADD COLUMN external_id VARCHAR(200)"))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_external_id ON sources(external_id)")
        )


def classify_category(description: str) -> str:
    upper = description.upper()
    if any(word in upper for word in _REPLACEMENT_WORDS):
        return "replacement"
    return "new_installation"


def find_airport_by_code(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            (Airport.iata_code == code) | (Airport.icao_code == code) | (Airport.faa_code == code)
        )
    )


def find_airport_by_city_state(session: Session, city: str, state: str) -> tuple[Airport | None, bool]:
    """Returns (airport, ambiguous). airport is None on zero OR ambiguous matches."""
    matches = session.scalars(
        select(Airport).where(
            Airport.city.is_not(None),
            Airport.state_region.is_not(None),
            Airport.city.ilike(city),
            Airport.state_region.ilike(state),
        )
    ).all()
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def resolve_airport(session: Session, grant: UsaspendingGrant) -> tuple[Airport | None, bool]:
    """Find or create the Airport this grant is about.

    Tries the embedded Loc ID first (most precise, e.g. state block grants
    naming a specific sub-recipient airport), then the standard beneficiary
    city/state sentence. Returns (None, False) only when neither pattern
    matches at all - e.g. a pure state block-grant-administration record with
    no specific airport named, which can't be attributed to anything.
    """
    loc_id_match = LOC_ID_PATTERN.search(grant.description)
    if loc_id_match:
        code = loc_id_match.group(1)
        airport = find_airport_by_code(session, code)
        if airport is not None:
            return airport, False
        # A real Loc ID we don't have yet - safe to create with it.
        airport = Airport(faa_code=code, name=grant.recipient_name.title(), country="USA")
        session.add(airport)
        session.flush()
        return airport, True

    beneficiary_match = BENEFICIARY_PATTERN.search(grant.description)
    if not beneficiary_match:
        return None, False

    city, state = beneficiary_match.groups()
    city = city.strip().title()
    state = state.strip().title()
    airport, ambiguous = find_airport_by_city_state(session, city, state)
    if airport is not None:
        return airport, False
    if ambiguous:
        return None, False  # more than one airport shares this city/state - don't guess

    airport = Airport(
        name=grant.recipient_name.title(),
        city=city,
        state_region=state,
        country="USA",
        notes=(
            "Name approximated from the USAspending grant recipient; no FAA "
            "Loc ID was available in the award description. Verify/correct "
            "manually if you find the airport's real identifiers."
        ),
    )
    session.add(airport)
    session.flush()
    return airport, True


def import_all(
    *,
    end_date: date | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "grants_fetched": 0,
        "airports_created": 0,
        "signals_created": 0,
        "already_imported": 0,
        "unattributable": 0,
    }
    try:
        grants = fetch_all_emas_grants(client=client, end_date=end_date or date.today())
        stats["grants_fetched"] = len(grants)

        with session_factory() as session:
            ensure_source_external_id_column(session.get_bind())

            for grant in grants:
                external_id = f"usaspending:{grant.external_id}"
                if session.scalar(select(Source).where(Source.external_id == external_id)):
                    stats["already_imported"] += 1
                    continue

                airport, created = resolve_airport(session, grant)
                if airport is None:
                    stats["unattributable"] += 1
                    continue
                if created:
                    stats["airports_created"] += 1

                source = Source(
                    title=f"USAspending grant: {grant.recipient_name.title()}",
                    source_type="usaspending_grant",
                    publisher=grant.awarding_agency,
                    url=f"https://www.usaspending.gov/award/{grant.external_id}",
                    document_reference=grant.award_id,
                    summary=grant.description,
                    published_date=grant.start_date,
                    retrieved_at=date.today(),
                    reliability_level="official",
                    external_id=external_id,
                )
                session.add(source)
                session.flush()

                confidence = "high"
                session.add(
                    Signal(
                        airport=airport,
                        source=source,
                        title=f"USAspending grant: {grant.recipient_name.title()} EMAS",
                        category=classify_category(grant.description),
                        confidence=confidence,
                        status="identified",
                        probability_score=DEFAULT_SCORE_BY_CONFIDENCE[confidence],
                        planning_year=grant.start_date.year if grant.start_date else None,
                        estimated_total_value_usd=grant.award_amount,
                        notes=grant.description,
                    )
                )
                stats["signals_created"] += 1
                session.commit()
    finally:
        if owns_client:
            client.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch every historical USAspending.gov grant mentioning 'Engineered "
            "Material Arresting System', match it to an Airport (creating one if "
            "needed), and record a high-confidence Signal for each."
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
    except (UsaspendingError, httpx.HTTPError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print(f"Grants fetched (all-time):     {stats['grants_fetched']}")
    print(f"Already imported (skipped):    {stats['already_imported']}")
    print(f"Airports created:              {stats['airports_created']}")
    print(f"Signals created:               {stats['signals_created']}")
    print(f"Unattributable (no airport):   {stats['unattributable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
