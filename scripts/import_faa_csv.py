from __future__ import annotations

import argparse
import csv
import re
from datetime import date
from pathlib import Path
from typing import Callable

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Airport, Incident, Installation, Source

DEFAULT_AIRPORTS_CSV = Path("emas_airports_usa.csv")
DEFAULT_INCIDENTS_CSV = Path("emas_incidents_usa.csv")
SOURCE_TITLE = "FAA EMAS Incidents and Installations map (verified CSV export)"

_TRAILING_CODE = re.compile(r"\s*\([A-Za-z0-9]{3,4}\)\s*$")
_MONTH_YEAR = re.compile(r"^\s*([A-Za-z]+)\s+(\d{4})\s*$")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def ensure_airport_coordinate_columns(bind=engine) -> None:
    """Add latitude/longitude to airports if an older database doesn't have them.

    Base.metadata.create_all() only creates missing tables, not missing columns
    on ones that already exist - this is the one-off ALTER TABLE for that,
    idempotent so it's safe to call every run.
    """
    existing = {c["name"] for c in inspect(bind).get_columns("airports")}
    with bind.begin() as connection:
        if "latitude" not in existing:
            connection.execute(text("ALTER TABLE airports ADD COLUMN latitude FLOAT"))
        if "longitude" not in existing:
            connection.execute(text("ALTER TABLE airports ADD COLUMN longitude FLOAT"))


def _clean_name(raw: str) -> str:
    """"Kodiak (ADQ)" -> "Kodiak"."""
    return _TRAILING_CODE.sub("", raw).strip()


def _parse_month_year(raw: str) -> date:
    match = _MONTH_YEAR.match(raw)
    if not match:
        raise ValueError(f"Unrecognized incident date {raw!r}: expected 'Month YYYY'.")
    month_name, year = match.groups()
    month = _MONTHS.get(month_name.strip().lower())
    if month is None:
        raise ValueError(f"Unrecognized month name {month_name!r} in {raw!r}.")
    return date(int(year), month, 1)


def _parse_float(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw)


def get_or_create_source(session: Session) -> Source:
    source = session.scalar(select(Source).where(Source.title == SOURCE_TITLE))
    if source is not None:
        return source
    source = Source(
        title=SOURCE_TITLE,
        source_type="faa_tableau",
        publisher="Federal Aviation Administration",
        url=settings.faa_emas_source_url,
        retrieved_at=date.today(),
        reliability_level="official",
    )
    session.add(source)
    session.flush()
    return source


def find_airport(session: Session, arpt_id: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            (Airport.iata_code == arpt_id)
            | (Airport.icao_code == arpt_id)
            | (Airport.faa_code == arpt_id)
        )
    )


def get_or_create_airport(session: Session, row: dict) -> tuple[Airport, bool]:
    arpt_id = row["ARPT_ID"].strip()
    airport = find_airport(session, arpt_id)
    latitude = _parse_float(row.get("Latitud (genererad)") or row.get("LATITUDE"))
    longitude = _parse_float(row.get("Longitud (genererad)") or row.get("LONGITUDE"))
    name_key = "ATTR(ARPT_NAME)" if "ATTR(ARPT_NAME)" in row else "ARPT_NAME"
    city_key = "ATTR(CITY)" if "ATTR(CITY)" in row else "CITY"
    state_key = "ATTR(STATE)" if "ATTR(STATE)" in row else "STATE"
    name = _clean_name(row[name_key])
    city = row[city_key].strip()
    state = row[state_key].strip().title()

    if airport is not None:
        if not airport.faa_code:
            airport.faa_code = arpt_id
        if not airport.city:
            airport.city = city
        if not airport.state_region:
            airport.state_region = state
        if airport.latitude is None:
            airport.latitude = latitude
        if airport.longitude is None:
            airport.longitude = longitude
        return airport, False

    airport = Airport(
        faa_code=arpt_id,
        name=name,
        city=city,
        state_region=state,
        country="USA",
        latitude=latitude,
        longitude=longitude,
    )
    session.add(airport)
    session.flush()
    return airport, True


def get_or_create_installation(
    session: Session, airport: Airport, installation_type: str, *, source: Source, notes: str | None
) -> tuple[Installation, bool]:
    installation = session.scalar(
        select(Installation).where(
            Installation.airport_id == airport.id,
            Installation.type == installation_type,
        )
    )
    if installation is not None:
        installation.source_id = source.id
        if notes and not installation.notes:
            installation.notes = notes
        return installation, False

    installation = Installation(
        airport=airport,
        source=source,
        type=installation_type,
        status="active",
        notes=notes,
    )
    session.add(installation)
    session.flush()
    return installation, True


def import_airports_csv(session: Session, csv_path: Path, *, source: Source) -> dict:
    stats = {"airports_created": 0, "airports_updated": 0, "installations_created": 0, "installations_updated": 0}
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if not row.get("ARPT_ID", "").strip():
                continue
            airport, created = get_or_create_airport(session, row)
            stats["airports_created" if created else "airports_updated"] += 1

            installation, inst_created = get_or_create_installation(
                session,
                airport,
                row["TYPE"].strip(),
                source=source,
                notes=f"FAA map region: {row['MAP_REGION'].strip()}" if row.get("MAP_REGION") else None,
            )
            stats["installations_created" if inst_created else "installations_updated"] += 1
    return stats


def get_or_create_incident(
    session: Session, airport: Airport, incident_date: date, *, source: Source, summary: str
) -> tuple[Incident, bool]:
    incident = session.scalar(
        select(Incident).where(
            Incident.airport_id == airport.id,
            Incident.incident_date == incident_date,
        )
    )
    if incident is not None:
        return incident, False

    incident = Incident(
        airport=airport,
        source=source,
        incident_date=incident_date,
        incident_type="EMAS activation",
        emas_engaged=True,
        summary=summary,
    )
    session.add(incident)
    session.flush()  # also fires the incident -> Signal rule
    return incident, True


def import_incidents_csv(session: Session, csv_path: Path, *, source: Source) -> dict:
    stats = {"incidents_created": 0, "incidents_skipped_existing": 0, "airports_not_found": []}
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            arpt_id = row.get("ARPT_ID", "").strip()
            if not arpt_id:
                continue
            airport = find_airport(session, arpt_id)
            if airport is None:
                stats["airports_not_found"].append(arpt_id)
                continue

            total_saved = row["TOTAL_CREW_AND_PASSENGERS_SAVED"].strip()
            num_incidents = row["NUM_INCIDENTS"].strip()
            dates = [part.strip() for part in row["INCIDENT_DATES"].split(";") if part.strip()]
            for raw_date in dates:
                incident_date = _parse_month_year(raw_date)
                summary = (
                    f"Recorded in FAA EMAS Incidents map ({raw_date}; day of month not "
                    f"reported by source, defaulted to the 1st). Total crew and "
                    f"passengers saved is an airport-level aggregate across all "
                    f"{num_incidents} recorded incident(s) here: {total_saved}."
                )
                _incident, created = get_or_create_incident(
                    session, airport, incident_date, source=source, summary=summary
                )
                if created:
                    stats["incidents_created"] += 1
                else:
                    stats["incidents_skipped_existing"] += 1
    return stats


def import_all(
    *,
    airports_csv: Path = DEFAULT_AIRPORTS_CSV,
    incidents_csv: Path = DEFAULT_INCIDENTS_CSV,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    with session_factory() as session:
        ensure_airport_coordinate_columns(session.get_bind())
        source = get_or_create_source(session)
        airport_stats = import_airports_csv(session, airports_csv, source=source)
        session.commit()

        incident_stats = import_incidents_csv(session, incidents_csv, source=source)
        session.commit()

    return {**airport_stats, **incident_stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import FAA EMAS installation and incident CSV exports into Airport/Installation/Incident."
    )
    parser.add_argument("--airports-csv", type=Path, default=DEFAULT_AIRPORTS_CSV)
    parser.add_argument("--incidents-csv", type=Path, default=DEFAULT_INCIDENTS_CSV)
    args = parser.parse_args(argv)

    stats = import_all(airports_csv=args.airports_csv, incidents_csv=args.incidents_csv)

    print(f"Airports created:        {stats['airports_created']}")
    print(f"Airports updated:        {stats['airports_updated']}")
    print(f"Installations created:   {stats['installations_created']}")
    print(f"Installations updated:   {stats['installations_updated']}")
    print(f"Incidents created:       {stats['incidents_created']}")
    print(f"Incidents already present: {stats['incidents_skipped_existing']}")
    if stats["airports_not_found"]:
        print(
            f"Incident rows skipped (airport not found): "
            f"{', '.join(stats['airports_not_found'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
