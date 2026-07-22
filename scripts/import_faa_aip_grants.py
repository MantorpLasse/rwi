from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa_aip_grants import (
    AipGrant,
    AipGrantsError,
    discover_grant_pdf_urls,
    parse_grant_pdf,
)
from app.database import SessionLocal
from app.models import Airport, Source
from app.services import add_source_and_flag_keywords


def _find_airport(session: Session, grant: AipGrant) -> Airport | None:
    return session.scalar(select(Airport).where(Airport.faa_code == grant.loc_id))


def _source_for(grant: AipGrant) -> Source:
    return Source(
        title=f"AIP grant: {grant.project_description}",
        source_type="aip_grant",
        publisher="Federal Aviation Administration",
        url=grant.source_pdf_url,
        document_reference=grant.loc_id,
        summary=grant.project_description,
        retrieved_at=date.today(),
        reliability_level="official",
    )


def import_year(
    year: int,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
) -> dict:
    """Fetch, parse, and ingest one fiscal year's AIP grant PDFs.

    Every grant at an airport we already track becomes a Source; rule 2
    (add_source_and_flag_keywords) decides whether it also becomes a
    low-confidence Signal. Grants at airports we don't track yet are
    counted but skipped - this only feeds airports already in the database.
    """

    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "pdfs": 0,
        "grants": 0,
        "matched": 0,
        "signals_created": 0,
        "unmatched_loc_ids": [],
    }
    try:
        pdf_urls = discover_grant_pdf_urls(year, client=client)
        with session_factory() as session:
            for pdf_url in pdf_urls:
                response = client.get(pdf_url, timeout=30.0)
                response.raise_for_status()
                grants = parse_grant_pdf(response.content, source_pdf_url=pdf_url)
                stats["pdfs"] += 1
                stats["grants"] += len(grants)
                for grant in grants:
                    airport = _find_airport(session, grant)
                    if airport is None:
                        stats["unmatched_loc_ids"].append(grant.loc_id)
                        continue
                    stats["matched"] += 1
                    signal = add_source_and_flag_keywords(
                        session, airport=airport, source=_source_for(grant)
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
            "Fetch and parse FAA AIP grant announcement PDFs for one fiscal year, "
            "creating a Source (and a low-confidence Signal on an EMAS/RSA keyword "
            "match) for every grant at an airport already in the database."
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
    print(f"Matched to tracked airports: {stats['matched']}")
    print(f"Signals created (keyword match): {stats['signals_created']}")
    unmatched = sorted(set(stats["unmatched_loc_ids"]))
    if unmatched:
        print(f"Unmatched Loc IDs ({len(unmatched)}): {', '.join(unmatched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
