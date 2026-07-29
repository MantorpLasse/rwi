"""One-off enrichment of Fulton County Executive Airport's (FTY) Runway 8/26
EMAS Signal with detail from two new primary sources:

1. The project's Draft Environmental Assessment (Runway 8/26 Runway Safety
   Improvements Project, April 2026, fultoncountyga.gov) - fetched and read
   in full (267 pages). Confirms exact EMAS bed dimensions, the "nonstandard
   EMAS" classification, displaced-threshold shifts, a development schedule,
   and a $32M project cost estimate that supersedes the prior note's
   unverified $13.4M googled figure. No contractor is named - the document
   predates bidding/award.
2. The Georgia Statewide Aviation System Plan (dot.ga.gov) - cited by the
   user for the exact RSA-deficiency dimensions at each runway end. No
   specific document URL was available, only the domain, so - per
   app/models/source.py's documented convention for sources without a
   public link - this Source row is created with url=None rather than a
   guessed URL; the citation lives in the note text instead.

Repoints Signal.source_id at the Draft EA (the signal only holds one
source_id; the old Master Plan Technical Report Draft source row is left in
place, just unlinked, per the pattern in scripts/update_lex_emas_details.py).
Also corrects estimated_total_value_usd from the old unverified $13.4M to
the Draft EA's verified $32M - the prior note explicitly flagged that figure
as "googlad/sammanställd, ej verifierad", so a verified replacement from a
primary source updates the structured field too, not just the note text.

Writes to Signal.source_notes, not Signal.notes - this is sourced research
with a citation, public in the static export (see app/models/signal.py's
source_notes docstring), not a private annotation.

Safe to re-run: both Sources are looked up before creating a duplicate (by
url for the Draft EA, by title for the GA plan, since it has no url), and
each note addition is guarded by checking whether its content already
appears in the signal's source_notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Signal, Source
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

DRAFT_EA_URL = (
    "https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Executive-Airport/"
    "FTY_RWY18_26_RSA_clean_11May2026_Package.pdf"
)

DRAFT_EA_NOTE = (
    "Bekräftat via Draft Environmental Assessment (Runway 8/26 Runway Safety "
    "Improvements Project, april 2026, fultoncountyga.gov, 267 sidor, läst i sin "
    "helhet): EMAS-bäddar planeras vid båda banändar (Runway 8 och Runway 26). "
    "Löptexten (kap 2, s. 6) anger 160×311 fot total footprint per bädd (35 fots "
    "lead-in/setback + 276 fots arrestor bed). Tabell 2.1 (kap 2, s. 11) anger "
    "bäddens mått specifikt till 121,5×276 fot plus 35 fots lead-in separat - "
    "notera avvikelsen mellan löptextens och tabellens breddsiffra (160' resp. "
    "121,5'), båda ur samma dokument, ej avstämt vidare. Klassificerat som "
    "\"nonstandard EMAS\": uppfyller kravet på att stoppa flygplan vid 70 knop, "
    "men inte kravet på 600 fots undershoot-skydd (FAA AC 150/5220-22B). "
    "Runway 8-tröskeln flyttas ca 200 fot österut (displaced threshold), Runway "
    "26 förlängs 261 fot - banans fysiska längd ökar från 5 797 fot till 6 058 "
    "fot. Byggfas-tidslinje (kap 1, s. 2): byggstart uppskattas till augusti "
    "2026, ca 150 dagars byggtid, troligen uppdelat i flera faser eftersom "
    "EMAS/PAPI/MALSR kräver tillverkning utanför plats. Ingen entreprenör "
    "namngiven ännu - dokumentet är ett utkast (Draft EA), före upphandling/"
    "tilldelning. Totalkostnad för hela Alternative B-projektet (RSA-"
    "förbättringar + taxiway-justering + hinderröjning, båda banändar): ca "
    "32 MUSD (2024 års prisnivå enligt tabellen, kostnadsökningar väntas) - "
    "ersätter den tidigare ogooglade/ouppdaterade 13,4 MUSD-uppskattningen "
    f"från 2026-07-24-anteckningen. Källa: {DRAFT_EA_URL}"
)

GA_PLAN_NOTE = (
    "RSA underdimensionerad 690×150 fot vid RWY 08-änden, 430×110 fot vid "
    "RWY 26-änden, jämfört med standardkravet 1000×500 fot (källa: Georgia "
    "Statewide Aviation System Plan, dot.ga.gov - exakt dokument-URL ej "
    "tillgänglig, endast domänen angiven)."
)


def get_or_create_source_without_url(session: Session, *, title: str, **fields) -> Source:
    """Same idempotency idea as get_or_create_source, but keyed on title
    instead of url - for a source with no public link (see module
    docstring and app/models/source.py's url comment)."""
    source = session.scalar(select(Source).where(Source.url.is_(None), Source.title == title))
    if source is not None:
        return source
    source = Source(title=title, url=None, **fields)
    session.add(source)
    session.flush()
    return source


def update_fty_signal(session: Session, *, today: date | None = None) -> tuple[Signal, bool]:
    today = today or date.today()

    fty = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "FTY", Airport.icao_code == "FTY", Airport.faa_code == "FTY"))
    )
    if fty is None:
        raise SystemExit("No airport with code=FTY.")

    signals = session.scalars(select(Signal).where(Signal.airport_id == fty.id)).all()
    if len(signals) != 1:
        raise SystemExit(f"Expected exactly one Signal for FTY, found {len(signals)}.")
    signal = signals[0]

    ea_source = get_or_create_source(
        session,
        url=DRAFT_EA_URL,
        title=(
            "Fulton County Executive Airport – Runway 8/26 Runway Safety Improvements "
            "Project, Draft Environmental Assessment (April 2026)"
        ),
        source_type="environmental_assessment",
        publisher="Fulton County",
        published_date=date(2026, 4, 1),
        summary=(
            "Draft NEPA Environmental Assessment for FTY's Runway 8/26 RSA project. "
            "Confirms nonstandard EMAS beds at both runway ends (160'x311' footprint per "
            "the narrative text; 121.5'x276' bed + 35' lead-in per Table 2.1), displaced "
            "thresholds (200' at Runway 8, 261' at Runway 26), a development schedule "
            "(construction likely begins August 2026, ~150 days, phased for off-site "
            "EMAS/PAPI/MALSR manufacturing), and a $32M total project cost estimate "
            "(2024 pricing). No contractor named - pre-bid."
        ),
    )

    ga_plan_source = get_or_create_source_without_url(
        session,
        title="Georgia Statewide Aviation System Plan",
        source_type="state_aviation_system_plan",
        publisher="Georgia Department of Transportation (GDOT)",
        retrieved_at=today,
        summary=(
            "Statewide aviation system plan citing FTY's RSA deficiency in feet: "
            "690x150 at the Runway 8 end, 430x110 at the Runway 26 end, versus the "
            "1000x500 standard. Cited by domain only (dot.ga.gov) - no specific "
            "document URL available."
        ),
    )

    signal.source = ea_source
    signal.estimated_total_value_usd = 32_000_000

    # Public - sourced research with a citation, see Signal.source_notes'
    # docstring in app/models/signal.py. Not signal.notes (private).
    updated = False
    if DRAFT_EA_URL not in (signal.source_notes or ""):
        signal.source_notes = append_note(signal.source_notes, DRAFT_EA_NOTE, on=today)
        updated = True
    if "Georgia Statewide Aviation System Plan" not in (signal.source_notes or ""):
        signal.source_notes = append_note(signal.source_notes, GA_PLAN_NOTE, on=today)
        updated = True

    session.commit()
    session.refresh(signal)
    return signal, updated


def main() -> None:
    with SessionLocal() as session:
        signal, updated = update_fty_signal(session)
    print(
        f"Signal {signal.id} updated={updated}, source_id={signal.source_id}, "
        f"estimated_total_value_usd={signal.estimated_total_value_usd}"
    )


if __name__ == "__main__":
    main()
