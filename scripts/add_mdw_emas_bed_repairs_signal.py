"""One-off enrichment: add a Signal for Chicago Midway's (MDW) CIP project
46109 "EMAS Bed Repairs", found by searching the City of Chicago's official
2025-2029 Capital Improvement Program (CIP) Report (chicago.gov, 349 pages,
fetched and read in full - the site blocks bots without a browser-like
User-Agent header, which is likely why the user's own fetch attempt failed)
for EMAS/RSA-related line items.

Project 46109 is the *only* EMAS/RSA-related entry in the entire document -
nothing equivalent exists for O'Hare (ORD). It falls on PDF page 34, inside
the "AVIATION - MIDWAY" section (bounded by the "Midway Airport" header on
the prior page and the "MIDWAY Total" line right after it, before the
"AVIATION - O'HARE" section begins) - unambiguously a MDW project, not ORD.

No existing MDW Signal/Installation matches this project (MDW's only
existing EMAS-related Signal, id 12, is a broad "lifecycle watch" item, not
this specific budgeted repair), so this is a new Signal rather than an
update to an existing row. category=maintenance, confidence=high (an
official, budgeted CIP line item, not a rumor or a study). No runway is
named in the source for this line item - unlike its neighbors (46110
"Runway 4R/22L Pavement Rehabilitation", 46117 "Runway 4L/22R
Rehabilitation"), which do name a runway - so runway_id is deliberately
left unset rather than guessed.

Safe to re-run: the Source is looked up by url before creating a
duplicate, and the Signal is looked up by (airport_id, title) before
creating a duplicate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Signal, Source
from scripts.add_brazil_expansion import get_or_create_source

CIP_URL = (
    "https://www.chicago.gov/content/dam/city/depts/obm/supp_info/CIP/"
    "City%20of%20Chicago%202025-2029%20CIP.pdf"
)

SIGNAL_TITLE = "EMAS Bed Repairs (CIP-projekt 46109)"

SOURCE_NOTES = (
    "Bekräftat via City of Chicagos officiella 2025-2029 Capital Improvement "
    "Program (CIP) Report (chicago.gov, 349 sidor, läst i sin helhet). "
    "CIP-projekt 46109 \"EMAS Bed Repairs\", PDF-sida 34, i rapportens "
    "\"AVIATION - MIDWAY\"-sektion (mellan flygplatsens egen rubrikrad och "
    "\"MIDWAY Total\"-summeringen, före O'Hare-sektionen börjar) - entydigt "
    "ett Midway-projekt. Design-fas 2025, finansiering \"0603 - Bond\", "
    "totalt 880 000 USD (endast designfasen budgeterad i denna rapport - "
    "ingen byggfas listad ännu). Ingen specifik bana angiven i källan - till "
    "skillnad från grannposterna 46110 (\"Runway 4R/22L Pavement "
    "Rehabilitation\") och 46117 (\"Runway 4L/22R Rehabilitation\"), som "
    "namnger sina banor explicit, saknar 46109 en bankoppling i dokumentet; "
    "lämnas därför okopplad snarare än gissad. Hela dokumentet genomsökt "
    "efter EMAS/RSA-relaterade poster: detta är den enda träffen totalt - "
    "inget motsvarande hittades för O'Hare (ORD)."
)


def add_mdw_emas_bed_repairs_signal(session: Session) -> tuple[Signal, bool]:
    mdw = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "MDW", Airport.icao_code == "MDW", Airport.faa_code == "MDW"))
    )
    if mdw is None:
        raise SystemExit("No airport with code=MDW.")

    existing = session.scalar(
        select(Signal).where(Signal.airport_id == mdw.id, Signal.title == SIGNAL_TITLE)
    )
    if existing is not None:
        return existing, False

    cip_source = get_or_create_source(
        session,
        url=CIP_URL,
        title="City of Chicago 2025-2029 Capital Improvement Program (CIP) Report",
        source_type="CIP",
        publisher="City of Chicago Office of Budget and Management",
        published_date=date(2025, 11, 10),  # PDF CreationDate metadata - no separate release date printed in the document
        page_number="34",
        summary=(
            "City of Chicago's official 2025-2029 Capital Improvement Program report. "
            "Project 46109 'EMAS Bed Repairs' (Midway) is the only EMAS/RSA-related line "
            "item in the entire 349-page document - none exists for O'Hare. Design phase "
            "2025, funded '0603 - Bond', $880,000 total, no runway specified, no "
            "construction phase listed yet."
        ),
    )

    signal = Signal(
        airport=mdw,
        source=cip_source,
        title=SIGNAL_TITLE,
        category="maintenance",
        confidence="high",
        status="design",
        planning_year=2025,
        estimated_total_value_usd=880_000,
        estimated_emas_value_usd=880_000,  # the whole line item is EMAS work
        probability_score=8.0,  # matches this codebase's high-confidence default (see DEFAULT_SCORE_BY_CONFIDENCE)
        source_notes=SOURCE_NOTES,
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal, True


def main() -> None:
    with SessionLocal() as session:
        signal, created = add_mdw_emas_bed_repairs_signal(session)
    print(f"Signal {signal.id} created={created}, source_id={signal.source_id}")


if __name__ == "__main__":
    main()
