"""Import FAA IIJA (Infrastructure Investment and Jobs Act) grant announcements.

IIJA money is a separate, earmarked pot from the regular AIP entitlement/
discretionary grants (see scripts/import_faa_aip_grants.py) and from the
USAspending.gov award data (scripts/import_usaspending_grants.py) - same
FAA "Announcement" PDF table format, reused via
app.acquisition.faa_iija_grants.parse_grant_pdf, but its own Source.source_type
("iija_grant") so the two funding pots stay distinguishable in the data.

Six PDFs per fiscal year at a fixed URL pattern (no HTML listing page to
scrape, unlike AIP). Every grant at an airport we already track becomes a
Source; rule 2 (add_source_and_flag_keywords) decides whether it also becomes
a low-confidence Signal. Grants at airports we don't track yet are counted
but skipped.

Caveat inherited from the AIP/USAspending overlap (see
scripts/import_faa_aip_grants.py's module docstring): this script has no
shared external ID with USAspending, so the same underlying grant can show
up as a Source from both pipelines. Within this script alone, Source.external_id
(f"iija:{year}:{announcement}:{loc_id}") prevents re-importing the same PDF
row twice; it does not dedup against usaspending_grant/aip_grant Sources for
the same award. Check for near-duplicate Signals at the same airport before
trusting a fresh run's signals_created count.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Callable

import httpx
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.acquisition.faa_iija_grants import (
    AipGrant,
    AipGrantsError,
    discover_iija_grant_pdf_urls,
    parse_grant_pdf,
)
from app.database import SessionLocal, engine
from app.models import Airport, Source
from app.services import add_source_and_flag_keywords


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


def _find_airport(session: Session, grant: AipGrant) -> Airport | None:
    return session.scalar(select(Airport).where(Airport.faa_code == grant.loc_id))


def _source_for(grant: AipGrant, *, external_id: str) -> Source:
    return Source(
        title=f"IIJA grant: {grant.project_description}",
        source_type="iija_grant",
        publisher="Federal Aviation Administration",
        url=grant.source_pdf_url,
        document_reference=grant.loc_id,
        summary=grant.project_description,
        retrieved_at=date.today(),
        reliability_level="official",
        external_id=external_id,
    )


def import_year(
    year: int,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
) -> dict:
    """Fetch, parse, and ingest one fiscal year's six IIJA grant PDFs."""

    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "pdfs": 0,
        "grants": 0,
        "matched": 0,
        "already_imported": 0,
        "signals_created": 0,
        "unmatched_loc_ids": [],
    }
    try:
        pdf_urls = discover_iija_grant_pdf_urls(year)
        with session_factory() as session:
            ensure_source_external_id_column(session.get_bind())

            for announcement, pdf_url in enumerate(pdf_urls, start=1):
                response = client.get(pdf_url, timeout=30.0)
                response.raise_for_status()
                grants = parse_grant_pdf(response.content, source_pdf_url=pdf_url)
                stats["pdfs"] += 1
                stats["grants"] += len(grants)
                for grant in grants:
                    external_id = f"iija:{year}:{announcement}:{grant.loc_id}"
                    if session.scalar(select(Source).where(Source.external_id == external_id)):
                        stats["already_imported"] += 1
                        continue

                    airport = _find_airport(session, grant)
                    if airport is None:
                        stats["unmatched_loc_ids"].append(grant.loc_id)
                        continue
                    stats["matched"] += 1
                    signal = add_source_and_flag_keywords(
                        session, airport=airport, source=_source_for(grant, external_id=external_id)
                    )
                    if signal is not None:
                        stats["signals_created"] += 1
                session.commit()
    finally:
        if owns_client:
            client.close()
    return stats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and parse FAA IIJA grant announcement PDFs for one fiscal year, "
            "creating a Source (source_type=iija_grant) and a low-confidence Signal "
            "on an EMAS/RSA keyword match for every grant at an airport already in "
            "the database."
        )
    )
    parser.add_argument("--year", type=int, required=True, help="Fiscal year, e.g. 2026")
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if not args.allow_live_network:
        print("Refusing import: --allow-live-network is required.", file=sys.stderr)
        return 2
    if not args.allow_database_write:
        print("Refusing import: --allow-database-write is required.", file=sys.stderr)
        return 2

    try:
        stats = import_year(args.year)
    except (AipGrantsError, httpx.HTTPError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print(f"PDFs processed: {stats['pdfs']}")
    print(f"Grant rows parsed: {stats['grants']}")
    print(f"Already imported (skipped): {stats['already_imported']}")
    print(f"Matched to tracked airports: {stats['matched']}")
    print(f"Signals created (keyword match): {stats['signals_created']}")
    unmatched = sorted(set(stats["unmatched_loc_ids"]))
    if unmatched:
        print(f"Unmatched Loc IDs ({len(unmatched)}): {', '.join(unmatched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
