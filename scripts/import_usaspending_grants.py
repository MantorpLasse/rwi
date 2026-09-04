from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

import httpx
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.acquisition.usaspending_grants import (
    UsaspendingError,
    UsaspendingGrant,
    fetch_all_emas_grants,
)
from app.database import SessionLocal, engine
from app.models import Airport, Source, SourceAssertion

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

# resolve_airport() outcomes (docs/domain/usaspending-airport-resolution-fail-closed-report.md).
# RESOLVED_NEW ("resolved_new" - the resolver used to construct a new
# Airport from an embedded, real-shaped but unmatched Loc ID) was removed by
# RWI HQ "USAspending Airport Resolver Hardening": resolve_airport() can no
# longer create an Airport under any circumstance, so that outcome is no
# longer reachable and the constant is gone, not merely dead-branched.
# Caller recon (that mission's own Part 1) confirmed no code outside this
# module and its own tests ever imported or depended on it.
RESOLVED_EXISTING = "resolved_existing"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class AirportResolution:
    """Result of resolving one USAspending grant to a canonical Airport.

    UNRESOLVED means airport identity could not be established to a
    deterministic standard - no Airport is created and none is guessed.
    raw_identifier/raw_name/reason preserve what the grant text actually
    said, so the grant's evidence stays reviewable even without a
    canonical Airport link (see import_all() below)."""

    status: str
    airport: Optional[Airport] = None
    raw_identifier: Optional[str] = None
    raw_name: Optional[str] = None
    reason: Optional[str] = None


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


def _format_amount(amount: Decimal | None) -> str:
    if amount is None:
        return "okänt belopp"
    value = float(amount)
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def signal_title(grant: UsaspendingGrant) -> str:
    """One grant's title, distinct from every other grant at the same airport.

    Every grant used to become "USAspending grant: {recipient} EMAS" -
    identical across all of an airport's grants (see
    docs/utredning_2026-07-26-del4.md), which read as duplicates in the
    signals list. Amount + fiscal year make each one recognizable at a glance.
    """
    fiscal_year = grant.start_date.year if grant.start_date else None
    amount_text = _format_amount(grant.award_amount)
    if fiscal_year:
        return f"USAspending grant — {amount_text}, FY{fiscal_year}"
    return f"USAspending grant — {amount_text}"


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


def resolve_airport(session: Session, grant: UsaspendingGrant) -> AirportResolution:
    """Resolve the Airport this grant is about, or fail closed.

    Tries the embedded Loc ID first (most precise, e.g. state block grants
    naming a specific sub-recipient airport with an actual FAA/ICAO/IATA-
    shaped code), then the standard beneficiary city/state sentence matched
    against an EXISTING Airport.

    NEVER creates an Airport, under any circumstance (RWI HQ "USAspending
    Airport Resolver Hardening" - the established invariant "machine
    evidence alone must not create Airport" now holds inside this resolver
    itself, not merely as an orchestration-level safety net in import_all()).
    A real, embedded Loc ID that does not match an existing Airport is
    UNRESOLVED, exactly like every other unmatched case - the identifier
    itself is preserved (raw_identifier) for later human review, but no
    Airport is fabricated from it. Previously (pre-hardening) this branch
    treated a real FAA/ICAO/IATA-shaped identifier as "sufficient grounds"
    to create a new Airport on the spot; that reasoning is retired.

    A grant's `recipient_name` is who received the money, not necessarily
    the airport: it can be an airport AUTHORITY operating more than one
    facility (see the Allegheny County Airport Authority case in
    docs/domain/usaspending-airport-resolution-fail-closed-report.md, where
    an earlier fallback fabricated an Airport named after the authority
    instead of the airport it actually funded). Every unmatched case - no
    pattern match at all, an embedded Loc ID with no existing Airport, an
    ambiguous city/state match, or a city/state match with zero existing
    Airport - is UNRESOLVED: identity is left unset rather than guessed,
    per RWI's fail-closed principle.
    """
    loc_id_match = LOC_ID_PATTERN.search(grant.description)
    if loc_id_match:
        code = loc_id_match.group(1)
        airport = find_airport_by_code(session, code)
        if airport is not None:
            return AirportResolution(RESOLVED_EXISTING, airport=airport, raw_identifier=code)
        # A real Loc-ID-shaped identifier was found, but no governed Airport
        # exists for it yet - fail closed rather than fabricate one.
        return AirportResolution(
            UNRESOLVED,
            raw_identifier=code,
            raw_name=grant.recipient_name.title(),
            reason=(
                "a Loc ID matching the expected FAA/ICAO/IATA shape was found in the grant description, "
                "but no existing Airport has this code - machine evidence alone must not create an "
                "Airport; this grant requires human review before any Airport is created for it"
            ),
        )

    beneficiary_match = BENEFICIARY_PATTERN.search(grant.description)
    if not beneficiary_match:
        return AirportResolution(
            UNRESOLVED,
            raw_name=grant.recipient_name.title(),
            reason="no FAA Loc ID and no beneficiary city/state sentence found in the grant description",
        )

    city, state = beneficiary_match.groups()
    city = city.strip().title()
    state = state.strip().title()
    raw_identifier = f"{city}, {state}"
    airport, ambiguous = find_airport_by_city_state(session, city, state)
    if airport is not None:
        return AirportResolution(RESOLVED_EXISTING, airport=airport, raw_identifier=raw_identifier)
    if ambiguous:
        return AirportResolution(
            UNRESOLVED,
            raw_identifier=raw_identifier,
            raw_name=grant.recipient_name.title(),
            reason="more than one existing Airport shares this beneficiary city/state - identity is ambiguous",
        )

    # No FAA/ICAO/IATA identifier, and no existing Airport for this
    # city/state: the recipient's own name is not sufficient grounds to
    # create canonical Airport identity (it may be an operating authority,
    # not the airport itself - see the docstring above). Fail closed
    # instead of fabricating a row from an organization name.
    return AirportResolution(
        UNRESOLVED,
        raw_identifier=raw_identifier,
        raw_name=grant.recipient_name.title(),
        reason=(
            "no FAA Loc ID and no existing Airport for this beneficiary city/state - "
            "a recipient organization name alone is not sufficient airport identity"
        ),
    )


def import_all(
    *,
    end_date: date | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    client: httpx.Client | None = None,
) -> dict:
    """RWI HQ "USAspending Stage-Only Conversion" (Slice C of the funding
    staging arc, following Slice A/known_airport_evidence_persistence and
    Slice B/known_airport_funding_reviewer_action+signal_creation): every
    grant - resolved or unresolved - is preserved as funding EVIDENCE only
    (Source + SourceAssertion, assertion_type="project_construction"). This
    function never creates a Signal, never records a ReviewerAction, and
    never calls either Slice B lightweight promotion service - Signal
    creation is a separate, later, explicitly human-approved step (see
    app.services.known_airport_funding_reviewer_action /
    known_airport_funding_signal_creation), never automatic on import.

    AIRPORT-FABRICATION SAFETY: resolve_airport() itself can no longer
    create an Airport under any circumstance (RWI HQ "USAspending Airport
    Resolver Hardening") - a real embedded Loc ID with no matching existing
    Airport is UNRESOLVED, like every other unmatched case. This function
    used to need an orchestration-level rollback workaround (RESOLVED_NEW
    detection + session.rollback()) to protect against a fabrication that
    happened inside resolve_airport() itself, before this function ever saw
    the result (RWI HQ "USAspending Stage-Only Conversion", Slice C's own
    Part 5 finding); that workaround has been removed as genuinely
    unreachable now that the resolver fails closed on its own.

    AMOUNT SAFETY: grant.award_amount is preserved only inside the raw
    evidence text (SourceAssertion.raw_relevant_text, the grant's own
    description, verbatim) - never copied into any structured value field.
    No estimated_total_value_usd, no estimated_emas_value_usd, no supplier
    field, is populated or inferred anywhere in this function.
    """
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "RunwaySafeIntelligence/1.0"}
    )
    stats = {
        "grants_fetched": 0,
        "evidence_staged_resolved": 0,
        "evidence_staged_unresolved": 0,
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

                # resolve_airport() itself never creates an Airport - see
                # this function's own docstring, "AIRPORT-FABRICATION
                # SAFETY". No rollback workaround is needed here any more.
                resolution = resolve_airport(session, grant)

                # The Source is created regardless of resolution outcome -
                # it needs no Airport link, so a grant whose airport
                # identity can't be established (or whose only path to one
                # would have fabricated an Airport) still keeps its
                # evidence (title, recipient, description, award reference,
                # URL) instead of vanishing without a trace.
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

                if resolution.status == UNRESOLVED:
                    # No Signal - identity is not established. The raw
                    # values are preserved on a SourceAssertion instead
                    # (airport_id left NULL), the repository's existing
                    # mechanism for evidence recorded before identity
                    # reconciliation - available for later human review,
                    # never silently discarded and never guessed.
                    session.add(
                        SourceAssertion(
                            source_id=source.id,
                            airport_id=None,
                            assertion_type="project_construction",
                            raw_airport_identifier=resolution.raw_identifier,
                            raw_airport_name=resolution.raw_name,
                            raw_relevant_text=grant.description,
                            source_record_identifier=external_id,
                            evidence_quality="unverified_candidate",
                            review_state="unreviewed",
                        )
                    )
                    stats["unattributable"] += 1
                    stats["evidence_staged_unresolved"] += 1
                    session.commit()
                    continue

                # RESOLVED_EXISTING: identity is already known. Stage
                # funding evidence only, through the SAME lightweight field
                # contract app.services.known_airport_evidence_persistence
                # guarantees for assertion_type="project_construction"
                # (evidence_quality="unverified_candidate",
                # review_state="unreviewed", every identity/intelligence/
                # promotion decision field left NULL) - exactly the shape
                # app.services.known_airport_funding_lightweight_path_guard
                # checks for. Constructed directly here rather than via
                # apply_known_airport_evidence_persistence() itself: that
                # function manages its own Source under a
                # "discovery:{document_identity}" external_id convention,
                # which this importer's own top-level "usaspending:
                # {external_id}" already-imported check (above) would not
                # recognize on replay - reusing it as-is would silently
                # break this importer's own pre-existing idempotency
                # contract. No Signal, no ReviewerAction, no call to either
                # Slice B lightweight promotion service - human review is a
                # separate, later, explicitly-authorized step.
                session.add(
                    SourceAssertion(
                        source_id=source.id,
                        airport_id=resolution.airport.id,
                        assertion_type="project_construction",
                        raw_airport_identifier=resolution.raw_identifier,
                        raw_relevant_text=grant.description,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch every historical USAspending.gov grant mentioning 'Engineered "
            "Material Arresting System' and preserve each as funding evidence "
            "(Source + SourceAssertion, assertion_type='project_construction') - "
            "never a Signal, never an Airport created from machine evidence alone. "
            "Human review and Signal promotion are separate, later, explicitly "
            "authorized steps (see app.services.known_airport_funding_reviewer_action "
            "/ known_airport_funding_signal_creation)."
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

    print(f"Grants fetched (all-time):        {stats['grants_fetched']}")
    print(f"Already imported (skipped):       {stats['already_imported']}")
    print(f"Evidence staged (known Airport):  {stats['evidence_staged_resolved']}")
    print(f"Evidence staged (unresolved):     {stats['evidence_staged_unresolved']}")
    print(f"Unattributable (no airport):      {stats['unattributable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
