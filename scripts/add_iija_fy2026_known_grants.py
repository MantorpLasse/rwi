"""One-off import of three FY2026 IIJA grant announcements, verified against
live FAA IIJA Announcement PDFs (source_pdf_url below), each matching an
airport we already track:

- MHT (Manchester-Boston): "Reconstruct Engineered Material Arresting System
  Safety Area", $5,100,000, Announcement 4. Matches the existing MHT
  USAspending signal's PURPOSE text almost verbatim (Signal.notes starting
  "PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA") -
  same underlying RSA/EMAS reconstruction, IIJA is a separate funding pot
  reporting a different tranche amount than the USAspending total.
- BOS (Boston Logan): "Construct/Extend Safety Area", $17,500,000,
  Announcement 4. Matches the known RWY 27 Phase 2 signal (the flagship
  confirmed Signal, title "Runway 9/27 RSA and EMAS phase 2" - not one of
  the several USAspending Phase-2-worded signals at this airport).
- MMU (Morristown): "Construct/Extend Safety Area", $1,362,000,
  Announcement 6. Matches Morristown's sole existing signal (from
  USAspending; the airport row itself has no FAA Loc ID yet, backfilled
  here to "MMU" so future automated IIJA/AIP imports can match it too).

Per explicit instruction: each of these three IIJA grants is a SEPARATE
funding pot from AIP/USAspending (Source.source_type="iija_grant", not
"aip_grant"/"usaspending_grant"). "Attach, don't create a new Signal" is
implemented as: create a standalone iija_grant Source for provenance/dedup
(get_or_create by external_id, same "iija:{year}:{announcement}:{loc_id}"
scheme scripts/import_faa_iija_grants.py uses, so a later recurring-importer
run recognizes these as already imported), and append a dated note to the
existing Signal - Signal.source_id is left pointing at the original
USAspending source, since a Signal only holds one source_id and that
source's detailed PURPOSE text is worth keeping as the primary citation.

Also flags, on Charlotte's (CLT) existing Runway Safe EMAS-order Signal,
that CLT separately has an IIJA grant for "Expand/Reconstruct Apron" -
apron work, nothing to do with the EMAS order or these three grants. Not
imported here (no verified amount/announcement in hand), just guarded
against future confusion since CLT is a name that will recur in later IIJA
PDFs and an automated importer must not attribute an unrelated apron grant
to the EMAS-order signal.

Safe to re-run: each Source is looked up by external_id before creating a
duplicate, and each note-append is guarded by checking whether that
external_id already appears in the target Signal's notes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa_iija_grants import iija_grant_pdf_url
from app.database import SessionLocal
from app.models import Airport, Signal, Source
from scripts.annotate_signal import append_note
from scripts.import_usaspending_grants import find_airport_by_code

FISCAL_YEAR = 2026
CLT_APRON_GUARD_NOTE = (
    "OBS: CLT har separat ett IIJA-bidrag för \"Expand/Reconstruct Apron\" "
    "(asfaltsyta, ej EMAS/RSA) - helt orelaterat till denna Runway "
    "Safe-order. Håll isär: blanda inte ihop apron-bidraget med "
    "EMAS-ordern nedan."
)


@dataclass(frozen=True)
class KnownIijaGrant:
    airport_code: str
    project_description: str
    amount_usd: Decimal
    announcement: int
    signal_finder_note_substring: str | None = None
    signal_finder_title_substring: str | None = None


KNOWN_GRANTS = [
    KnownIijaGrant(
        airport_code="MHT",
        project_description="Reconstruct Engineered Material Arresting System Safety Area",
        amount_usd=Decimal("5100000"),
        announcement=4,
        signal_finder_note_substring="RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA",
    ),
    KnownIijaGrant(
        airport_code="BOS",
        project_description="Construct/Extend Safety Area",
        amount_usd=Decimal("17500000"),
        announcement=4,
        signal_finder_title_substring="phase 2",
    ),
    KnownIijaGrant(
        airport_code="MMU",
        project_description="Construct/Extend Safety Area",
        amount_usd=Decimal("1362000"),
        announcement=6,
        signal_finder_note_substring="CONSTRUCT/EXTEND SAFETY AREA",
    ),
]


def ensure_morristown_faa_code(session: Session) -> Airport | None:
    """Backfill the FAA Loc ID onto the Morristown airport row.

    scripts/import_usaspending_grants.py created this airport from a grant's
    recipient name/beneficiary sentence, with no Loc ID available at the
    time (see the airport's own notes). The IIJA finding confirms it as
    "MMU" - filling this in lets faa_code-based matching (this script,
    and the recurring AIP/IIJA importers) find it going forward.
    """
    morristown = session.scalar(
        select(Airport).where(Airport.city == "Morristown", Airport.state_region == "New Jersey")
    )
    if morristown is not None and morristown.faa_code is None:
        morristown.faa_code = "MMU"
    return morristown


def get_or_create_iija_source(session: Session, grant: KnownIijaGrant, *, external_id: str) -> Source:
    existing = session.scalar(select(Source).where(Source.external_id == external_id))
    if existing is not None:
        return existing
    source = Source(
        title=f"IIJA grant: {grant.project_description}",
        source_type="iija_grant",
        publisher="Federal Aviation Administration",
        url=iija_grant_pdf_url(FISCAL_YEAR, grant.announcement),
        document_reference=grant.airport_code,
        summary=grant.project_description,
        retrieved_at=date.today(),
        reliability_level="official",
        external_id=external_id,
    )
    session.add(source)
    session.flush()
    return source


def find_target_signal(session: Session, airport: Airport, grant: KnownIijaGrant) -> Signal:
    query = select(Signal).where(Signal.airport_id == airport.id)
    if grant.signal_finder_note_substring:
        query = query.where(Signal.notes.icontains(grant.signal_finder_note_substring))
    elif grant.signal_finder_title_substring:
        query = query.where(Signal.title.icontains(grant.signal_finder_title_substring))
    signals = session.scalars(query).all()
    if len(signals) != 1:
        raise SystemExit(
            f"Expected exactly one matching Signal for {grant.airport_code}, found {len(signals)}."
        )
    return signals[0]


def apply_known_grant(session: Session, grant: KnownIijaGrant, *, today: date) -> tuple[Signal, bool]:
    airport = find_airport_by_code(session, grant.airport_code)
    if airport is None:
        raise SystemExit(f"No airport with code={grant.airport_code} - run the USAspending import first.")

    external_id = f"iija:{FISCAL_YEAR}:{grant.announcement}:{grant.airport_code}"
    source = get_or_create_iija_source(session, grant, external_id=external_id)

    signal = find_target_signal(session, airport, grant)
    if external_id in (signal.notes or ""):
        return signal, False

    note = (
        f"IIJA-bidrag (Announcement {grant.announcement}, FY{FISCAL_YEAR}, {external_id}): "
        f"\"{grant.project_description}\", ${grant.amount_usd:,.0f}. {source.url} - separat "
        "finansieringspott (IIJA) utöver ovanstående AIP/USAspending-bidrag; primär källa "
        "(source_id) lämnas oförändrad."
    )
    signal.notes = append_note(signal.notes, note, on=today)
    return signal, True


def apply_clt_guard_note(session: Session, *, today: date) -> bool:
    clt = find_airport_by_code(session, "CLT")
    if clt is None:
        raise SystemExit("No airport with code=CLT.")
    signal = session.scalar(
        select(Signal).where(Signal.airport_id == clt.id, Signal.confirmed_vendor.is_not(None))
    )
    if signal is None:
        raise SystemExit("No confirmed-vendor Signal for CLT - run scripts/add_rw_shareholder_letter_signals.py first.")
    if CLT_APRON_GUARD_NOTE in (signal.notes or ""):
        return False
    signal.notes = append_note(signal.notes, CLT_APRON_GUARD_NOTE, on=today)
    return True


def seed(session: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"signals_updated": 0, "signals_already_up_to_date": 0, "clt_guard_note_added": False}

    ensure_morristown_faa_code(session)

    for grant in KNOWN_GRANTS:
        _, updated = apply_known_grant(session, grant, today=today)
        if updated:
            stats["signals_updated"] += 1
        else:
            stats["signals_already_up_to_date"] += 1

    stats["clt_guard_note_added"] = apply_clt_guard_note(session, today=today)

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
