"""FALLBACK / DORMANT since 2026-07-22.

scripts/import_usaspending_grants.py is now the active source for EMAS grant
data (structured JSON, goes back to 2007, no PDF parsing needed - see
PLAN_FORENKLING.md's "USAspending.gov" section). This script is kept working
and tested as a fallback in case that API ever stops serving this data, but
isn't run as part of the normal refresh flow.

RWI HQ "FAA AIP Parser + Stage-Only Conversion": every grant row - resolved
or unresolved - is now preserved as funding EVIDENCE only (Source + one
project_construction SourceAssertion), the SAME architecture
scripts/import_usaspending_grants.py uses (RWI HQ "USAspending Stage-Only
Conversion", Slice C). This script no longer calls
app.services.signal_rules.add_source_and_flag_keywords() (that shared
function - also used by scripts/import_faa_iija_grants.py, untouched by this
mission - creates a Source AND, on an EMAS/RSA keyword match, an automatic
low-confidence Signal with zero human review; this importer no longer wants
either the Signal or the keyword-flagging side effect). No Signal, no
ReviewerAction, no call to either Slice B lightweight promotion service is
ever created here - human review and Signal promotion are separate, later,
explicitly authorized steps.

Because the two scripts don't share a common external ID, cross-source
dedup between this script's staged evidence and USAspending's is not
automatic - a human reviewer comparing candidate evidence for the same
airport should check both.
"""

from __future__ import annotations

import argparse
import hashlib
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
from app.models import Airport, Source, SourceAssertion
from scripts.import_usaspending_grants import ensure_source_external_id_column


def _find_airport(session: Session, grant: AipGrant) -> Airport | None:
    """Exact FAA Loc ID match against an already-existing, already-governed
    Airport only - never fuzzy, never a name/city/state fallback, and never
    creates one. Unchanged from before this mission (Part 4 recon: this was
    already the only Airport-identity path in this importer - there is no
    reachable automatic Airport-creation branch here to remove)."""
    return session.scalar(select(Airport).where(Airport.faa_code == grant.loc_id))


def _external_id_for(grant: AipGrant) -> str:
    """The most stable identity available for one AIP grant row: unlike a
    USAspending award, an FAA AIP row has no globally unique record ID of
    its own - the announcement PDF plus the Loc ID plus a content hash of
    the row's own text is the most stable, deterministic identity this data
    shape offers. Namespaced ("faa_aip:") so it can never collide with the
    "usaspending:"/"discovery:" namespaces already used by sibling
    persistence paths in this pipeline."""
    content_hash = hashlib.sha256(grant.project_description.encode("utf-8")).hexdigest()[:16]
    return f"faa_aip:{grant.source_pdf_url}#{grant.loc_id}#{content_hash}"


def _evidence_text_for(grant: AipGrant) -> str:
    """The FAA project description, plus a legible, deterministic rendering
    of the row's own three funding amounts (RWI HQ 'FAA AIP Parser +
    Stage-Only Conversion', Part 5: amounts are preserved only as evidence
    TEXT here, never as a structured Signal/value field - a human reviewer
    can recover them from this text later, but this function never
    interprets or promotes them itself). Entitlement/Discretionary/Total AIP
    are three distinct funding-record amounts, not automatically an EMAS
    contract value, a total project value, or a supplier value - see this
    module's own docstring and the mission's own Part 5."""
    return (
        f"{grant.project_description} | Entitlement Amt: {grant.entitlement_amt}, "
        f"Discretionary Amt: {grant.discretionary_amt}, Total AIP: {grant.total_aip_amt}"
    )


def _source_for(grant: AipGrant, *, external_id: str) -> Source:
    return Source(
        title=f"AIP grant: {grant.project_description}",
        source_type="aip_grant",
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
    """Fetch, parse, and stage one fiscal year's AIP grant PDFs as funding
    evidence only - never a Signal, never a ReviewerAction, never an
    Airport. See this module's own docstring for the full architecture.
    """

    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "pdfs": 0,
        "grants": 0,
        "already_imported": 0,
        "evidence_staged_resolved": 0,
        "evidence_staged_unresolved": 0,
        "unresolved_loc_ids": [],
    }
    try:
        pdf_urls = discover_grant_pdf_urls(year, client=client)
        with session_factory() as session:
            ensure_source_external_id_column(session.get_bind())

            for pdf_url in pdf_urls:
                response = client.get(pdf_url, timeout=30.0)
                response.raise_for_status()
                grants = parse_grant_pdf(response.content, source_pdf_url=pdf_url)
                stats["pdfs"] += 1
                stats["grants"] += len(grants)

                for grant in grants:
                    external_id = _external_id_for(grant)
                    if session.scalar(select(Source).where(Source.external_id == external_id)):
                        stats["already_imported"] += 1
                        continue

                    airport = _find_airport(session, grant)
                    source = _source_for(grant, external_id=external_id)
                    session.add(source)
                    session.flush()

                    if airport is None:
                        # Identity not established - preserve the evidence
                        # (airport_id left NULL) rather than fabricating or
                        # discarding it, the same contract
                        # scripts/import_usaspending_grants.py's own
                        # UNRESOLVED path already uses.
                        session.add(
                            SourceAssertion(
                                source_id=source.id,
                                airport_id=None,
                                assertion_type="project_construction",
                                raw_airport_identifier=grant.loc_id,
                                raw_airport_name=grant.airport_name,
                                raw_relevant_text=_evidence_text_for(grant),
                                source_record_identifier=external_id,
                                evidence_quality="unverified_candidate",
                                review_state="unreviewed",
                            )
                        )
                        stats["evidence_staged_unresolved"] += 1
                        stats["unresolved_loc_ids"].append(grant.loc_id)
                    else:
                        # Known Airport: stage funding evidence only,
                        # through the SAME lightweight field contract
                        # app.services.known_airport_evidence_persistence
                        # guarantees for assertion_type="project_construction"
                        # - exactly the shape
                        # app.services.known_airport_funding_lightweight_path_guard
                        # checks for. No Signal, no ReviewerAction, no call
                        # to either Slice B lightweight promotion service -
                        # human review is a separate, later, explicitly
                        # authorized step.
                        session.add(
                            SourceAssertion(
                                source_id=source.id,
                                airport_id=airport.id,
                                assertion_type="project_construction",
                                raw_airport_identifier=grant.loc_id,
                                raw_relevant_text=_evidence_text_for(grant),
                                source_record_identifier=external_id,
                                evidence_quality="unverified_candidate",
                                review_state="unreviewed",
                            )
                        )
                        stats["evidence_staged_resolved"] += 1
                    session.commit()
    finally:
        if owns_client:
            client.close()
    return stats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and parse FAA AIP grant announcement PDFs for one fiscal year, "
            "preserving each row as funding evidence (Source + SourceAssertion, "
            "assertion_type='project_construction') - never a Signal, never an "
            "Airport created from machine evidence alone. Human review and Signal "
            "promotion are separate, later, explicitly authorized steps (see "
            "app.services.known_airport_funding_reviewer_action / "
            "known_airport_funding_signal_creation)."
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

    print(f"PDFs processed:                   {stats['pdfs']}")
    print(f"Grant rows parsed:                {stats['grants']}")
    print(f"Already imported (skipped):       {stats['already_imported']}")
    print(f"Evidence staged (known Airport):  {stats['evidence_staged_resolved']}")
    print(f"Evidence staged (unresolved):     {stats['evidence_staged_unresolved']}")
    unresolved = sorted(set(stats["unresolved_loc_ids"]))
    if unresolved:
        print(f"Unresolved Loc IDs ({len(unresolved)}): {', '.join(unresolved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
