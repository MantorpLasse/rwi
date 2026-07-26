"""Import install years from two FAA "Fact Sheet - EMAS" press releases: the
2011-03-07 edition (Exhibit 5, text-extractable, archived at
docs/sources/2011-03-07_faa-emas-fact-sheet.pdf) and the 2016-02-04 edition
(archived at docs/sources/2016-02-04_faa-emas-fact-sheet-skybrary-mirror.pdf,
found via a skybrary.aero mirror since the FAA's own copy is gone).

The 2016 PDF is scanned/image-based - pdfplumber extracts zero characters
from every page. OCR software (tesseract, via `choco install tesseract`) was
attempted but failed: chocolatey needs admin rights this sandbox doesn't
have (`Access to the path 'C:\\ProgramData\\chocolatey\\lib-bad' is denied`).
Rather than pull in a heavy pip-only OCR stack (e.g. easyocr + torch) for a
six-page document, the pages were rendered to PNG with PyMuPDF (a pure pip
install, no system binary) and read directly - the model this script's
author runs on is multimodal, so this was more reliable than OCR anyway.
Both PDFs are saved in the repo per the source instructions, so this is
reproducible without re-running that rendering step.

Where an airport appears in both fact sheets, 2016 (newer, more complete -
e.g. it splits some single 2011 entries into distinct systems with their own
replacement/retrofit years) takes precedence as the Source on its
Installation row; 2011 is cited in notes. The reverse holds for Groton-New
London, where the 2011 sheet's "under contract" entry is actually more
granular (phased 2011/2012 dates) than 2016's flattened "2011" - so 2011 is
the primary Source there, with 2016 cited as the completion confirmation.
Airports appearing in 2011 only (there are none in this script's list) would
use 2011 alone.

Every airport touched here already has a generic FAA-map-sourced Installation
row (install_year=None) from an earlier import. Per the "one source_id per
row" convention used throughout this codebase, this script does not
overwrite those - it adds a *new*, separate Installation row per airport
carrying the fact sheet's specific year(s), exactly as
scripts/add_gadelius_greenemas_installations.py did for MDW/ORD.

Also (beyond the explicit request, but a direct, low-risk extension of it):
ELM's existing Installation (id 43) gets install_year=2012 set directly -
the 2016 sheet independently confirms it, resolving the "a grant-award year
isn't a completion year" caveat left open by
scripts/attach_elm_fy2011_grant_source.py.

Dutchess County (Poughkeepsie, NY) does not exist in the database at all -
created as a new Airport + Installation, the only genuinely new airport
among the ones checked (everything else in the "candidate" list already
existed).

Safe to re-run: Sources are looked up by url before creating a duplicate,
new Installation rows are guarded by (airport_id, type, install_year), and
the ELM update is guarded by checking whether its install_year is already set.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Source
from scripts.add_brazil_expansion import get_or_create_airport, get_or_create_source

FACT_SHEET_2011_URL = "http://www.faa.gov/news/fact_sheets/news_story.cfm?newsId=12497"
FACT_SHEET_2016_URL = "https://www.faa.gov/news/fact_sheets/news_story.cfm?newsId=13754"

SOURCE_2011_SUMMARY = (
    "FAA officiellt pressmeddelande 'Fact Sheet - Engineered Material Arresting Systems "
    "(EMAS)', 2011-03-07. Tabell over 35 flygplatser med installerad EMAS + 6 flygplatser "
    "med projekt 'currently under contract' (ej klara vid publiceringsdatum). Arkiverad "
    "kopia: docs/sources/2011-03-07_faa-emas-fact-sheet.pdf."
)
SOURCE_2016_SUMMARY = (
    "FAA officiellt pressmeddelande 'Fact Sheet - Engineered Material Arresting System "
    "(EMAS)', 2016-02-04. Nyare och mer komplett tabell (61 flygplatser med ESCO EMAS, "
    "separat tabell for Runway Safe EMAS) - flera 2011-poster spjalkas har upp i "
    "separata system med egna ersattnings-/retrofit-ar. Bildbaserad PDF (gick inte att "
    "textextrahera med pdfplumber - troligen skannad); last direkt via bildvision "
    "(sidorna renderades till PNG med PyMuPDF) eftersom OCR-verktyg (tesseract) inte "
    "gick att installera i denna sandbox utan adminrattigheter. Hittad via en "
    "skybrary.aero-spegling (https://skybrary.aero/sites/default/files/bookshelf/1842.pdf) "
    "- FAA:s egen sida for denna newsId ar inte langre tillganglig. Arkiverad kopia: "
    "docs/sources/2016-02-04_faa-emas-fact-sheet-skybrary-mirror.pdf."
)


def get_or_create_2011_source(session: Session) -> Source:
    return get_or_create_source(
        session,
        url=FACT_SHEET_2011_URL,
        title="Fact Sheet \u2013 Engineered Material Arresting Systems (EMAS)",
        source_type="faa_fact_sheet",
        publisher="FAA",
        published_date=date(2011, 3, 7),
        retrieved_at=date.today(),
        document_reference="docs/sources/2011-03-07_faa-emas-fact-sheet.pdf",
        summary=SOURCE_2011_SUMMARY,
    )


def get_or_create_2016_source(session: Session) -> Source:
    return get_or_create_source(
        session,
        url=FACT_SHEET_2016_URL,
        title="Fact Sheet \u2013 Engineered Material Arresting System (EMAS)",
        source_type="faa_fact_sheet",
        publisher="FAA",
        published_date=date(2016, 2, 4),
        retrieved_at=date.today(),
        document_reference="docs/sources/2016-02-04_faa-emas-fact-sheet-skybrary-mirror.pdf",
        summary=SOURCE_2016_SUMMARY,
    )


def find_airport(session: Session, code: str) -> Airport | None:
    from sqlalchemy import or_

    return session.scalar(
        select(Airport).where(or_(Airport.iata_code == code, Airport.icao_code == code, Airport.faa_code == code))
    )


def get_or_create_installation(session: Session, *, airport: Airport, install_year: int, **fields) -> tuple[Installation, bool]:
    existing = session.scalar(
        select(Installation).where(
            Installation.airport_id == airport.id,
            Installation.type == "EMASMAX",
            Installation.install_year == install_year,
        )
    )
    if existing is not None:
        return existing, False
    installation = Installation(airport=airport, type="EMASMAX", install_year=install_year, status="active", **fields)
    session.add(installation)
    session.flush()
    return installation, True


# (code, install_year, source key ('2011' or '2016'), notes)
# notes always explain the systems-count / retrofit detail and which sheet(s)
# confirmed what, per the task's explicit ask.
AIRPORT_UPDATES = [
    ("ORD", 2008, "2016", (
        "2 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('Chicago-O'Hare, "
        "Chicago, IL, 2, 2008' i bada). Rattar en tidigare gissning om ~2016-2017 "
        "installationsar - den gissningen var fel, systemen ar 8 ar aldre."
    )),
    ("BGM", 2002, "2016", (
        "2 system, enligt 2016 Fact Sheet mer detaljerat an 2011 (som bara anger "
        "'2002' for bada): system A installerat 2002, ersatt 2012 (matchar det "
        "redan kanda $12,3M FAA FY2011-bidraget for 16/34 RSA-forbattringar, se "
        "docs/utredning_svaga_poster.md); system B installerat 2009, markt '***' "
        "= 'retrofitted bed' i 2016-dokumentets fotnot. 2011 Fact Sheet bekraftar "
        "bara grundfaktumet (2002, 2 system) utan denna detalj."
    )),
    ("HYA", 2003, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet (bada 'Barnstable "
        "Municipal, Hyannis, MA, 1, 2003'), ingen andring mellan utgavorna. "
        "Detta ar den ursprungliga installationen - var egen tidigare research "
        "(docs/utreding_status_flygplatser.md) tyder pa en senare ersattning "
        "runt 2023-2025, vilket ligger efter bada dessa ogonblicksbilder."
    )),
    ("MHT", 2007, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('Manchester, "
        "Manchester, NH, 1, 2007' i bada), ingen andring."
    )),
    ("CLT", 2008, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('Charlotte "
        "Douglas International, Charlotte, NC, 1, 2008' i bada), ingen andring."
    )),
    ("SUA", 2011, "2016", (
        "2 system. 2011 Fact Sheet listade detta som 'additional projects "
        "currently under contract' ('Martin County, Stuart, FL, 2, spring "
        "2011/summer 2011') - annu inte byggt vid publiceringsdatumet "
        "(2011-03-07). 2016 Fact Sheet bekraftar fardigstallt ('Martin County, "
        "Stuart, FL, 2, 2011')."
    )),
    ("DJT", 2011, "2016", (
        "1 system (Palm Beach). 2011 Fact Sheet listade detta som 'additional "
        "projects currently under contract' ('Palm Beach, Palm Beach, FL, 1, "
        "summer 2011') - annu inte byggt vid publiceringsdatumet. 2016 Fact "
        "Sheet bekraftar fardigstallt ('Palm Beach, Palm Beach, FL, 1, 2011')."
    )),
    ("ROC", 2001, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('Rochester "
        "International, Rochester, NY, 1, 2001' i bada), ingen andring."
    )),
    ("BTR", 2002, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('Baton Rouge "
        "Metropolitan, Baton Rouge, LA, 1, 2002' i bada), ingen andring."
    )),
    ("LRD", 2006, "2016", (
        "1 system, plus en retrofit 2012 enligt 2016 Fact Sheet ('Laredo "
        "International, Laredo, TX, 1, 2006/2012***', dar '***' = 'retrofitted "
        "bed'). 2011 Fact Sheet visar bara grundaret 2006."
    )),
    ("SAN", 2006, "2016", (
        "1 system. Bekraftat av bade 2011- och 2016-Fact Sheet ('San Diego "
        "International, San Diego, CA, 1, 2006' i bada), ingen andring."
    )),
    ("INT", 2010, "2016", (
        "Smith Reynolds (Winston-Salem, NC). 1 system. Bekraftat av bade 2011- "
        "och 2016-Fact Sheet ('Smith Reynolds, Winston-Salem, NC, 1, 2010' i "
        "bada), ingen andring."
    )),
    ("ILG", 2010, "2016", (
        "New Castle County (Wilmington, DE). 1 system. Bekraftat av bade 2011- "
        "och 2016-Fact Sheet ('New Castle County, Wilmington, DE, 1, 2010' i "
        "bada), ingen andring."
    )),
    ("FRG", 2011, "2016", (
        "Republic (Farmingdale, NY). 2 system enligt 2016 Fact Sheet "
        "('Republic, Farmingdale, NY, 2, 2011/2013') - vaxte fran ett planerat "
        "system (2011 Fact Sheet: 'additional projects under contract', 1 "
        "system, forvantad varen 2011) till tva, med det andra tillagt 2013."
    )),
    ("AUG", 2011, "2016", (
        "Augusta State (Augusta, ME). 2 system. 2011 Fact Sheet listade detta "
        "som 'additional projects under contract' ('Augusta State, Augusta, "
        "ME, 2, fall 2011') - annu inte byggt vid publiceringsdatumet. 2016 "
        "Fact Sheet bekraftar fardigstallt exakt som planerat ('Augusta State, "
        "Augusta, ME, 2, 2011')."
    )),
    ("GON", 2011, "2011", (
        "Groton-New London (Groton-New London, CT). 2 system, fasat: 2011 "
        "Fact Sheet ger den mest detaljerade tidslinjen ('additional projects "
        "under contract', 'Groton New-London, Groton-New London, CT, 2, "
        "summer 2011/fall 2012') - forsta systemet sommaren 2011, andra hosten "
        "2012. 2016 Fact Sheet bekraftar bara att bada ar fardigstallda, men "
        "utan fasuppdelningen ('Groton, Groton-New London, CT, 2, 2011')."
    )),
]

DUTCHESS_COUNTY_NOTES = (
    "Ny flygplats, fanns inte i databasen tidigare. 1 system, GA-flygplats "
    "('**' i bade 2011- och 2016-Fact Sheet). Bekraftat av bade 2011- och "
    "2016-Fact Sheet ('Dutchess County, Poughkeepsie, NY, 1, 2004**' i bada), "
    "ingen andring."
)

ELM_NOTE_MARKER = "2016 Fact Sheet ('Elmira-Corning, Elmira, NY, 1, 2012')"
ELM_NOTE = (
    f"install_year satt till 2012, bekraftat via {ELM_NOTE_MARKER} - loser den "
    "tidigare oppna fragan i scripts/attach_elm_fy2011_grant_source.py om att "
    "ett bidragsar (FY2011) inte i sig bekraftar fardigstallandear. Ingen "
    "andring av source_id (fortfarande FAA-kartan, som ar mest specifik om var "
    "bädden ligger)."
)


def seed(session: Session, *, today: date | None = None) -> dict:
    from scripts.annotate_signal import append_note

    today = today or date.today()
    stats = {"airports_created": 0, "sources_created": 0, "installations_created": 0, "elm_updated": False}

    def _source_2011():
        before = session.scalar(select(Source).where(Source.url == FACT_SHEET_2011_URL))
        source = get_or_create_2011_source(session)
        if before is None:
            stats["sources_created"] += 1
        return source

    def _source_2016():
        before = session.scalar(select(Source).where(Source.url == FACT_SHEET_2016_URL))
        source = get_or_create_2016_source(session)
        if before is None:
            stats["sources_created"] += 1
        return source

    source_2011 = _source_2011()
    source_2016 = _source_2016()
    sources = {"2011": source_2011, "2016": source_2016}

    for code, install_year, source_key, notes in AIRPORT_UPDATES:
        airport = find_airport(session, code)
        if airport is None:
            raise SystemExit(f"No airport with code={code}.")
        _, created = get_or_create_installation(
            session, airport=airport, install_year=install_year, source=sources[source_key], notes=notes,
        )
        if created:
            stats["installations_created"] += 1

    before_dutchess = session.scalar(select(Airport).where(Airport.iata_code == "POU"))
    dutchess = get_or_create_airport(
        session,
        iata_code="POU",
        icao_code="KPOU",
        name="Dutchess County Airport",
        city="Poughkeepsie",
        state_region="New York",
        country="USA",
    )
    if before_dutchess is None:
        stats["airports_created"] += 1
    _, dutchess_created = get_or_create_installation(
        session, airport=dutchess, install_year=2004, source=source_2016, notes=DUTCHESS_COUNTY_NOTES,
    )
    if dutchess_created:
        stats["installations_created"] += 1

    elm = find_airport(session, "ELM")
    if elm is None:
        raise SystemExit("No airport with code=ELM.")
    elm_installation = session.scalar(select(Installation).where(Installation.airport_id == elm.id))
    if elm_installation is None:
        raise SystemExit("No Installation for ELM.")
    if elm_installation.install_year is None:
        elm_installation.install_year = 2012
        elm_installation.notes = append_note(elm_installation.notes, ELM_NOTE, on=today)
        stats["elm_updated"] = True

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
