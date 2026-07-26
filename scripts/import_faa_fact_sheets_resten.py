"""Import the rest of the two FAA "Fact Sheet - EMAS" press releases (2011,
2016 - see scripts/import_faa_fact_sheets_2011_2016.py for how they were
sourced/read) not covered by that earlier script: every remaining airport
in both main tables, plus 2016's "additional projects currently under
contract" table.

Every single one of these ~46 airports already exists in the database with
a generic, year-less FAA-map-sourced Installation row - same situation as
the previous script, and the same fix: add a *new*, separate Installation
row per airport carrying the fact sheet's year(s), leaving the old row
untouched. 2016 is the source for all of them here (no case in this batch
where 2011 has more detail than 2016, unlike Groton-New London in the
previous script).

Three airports are handled differently because neither fact sheet actually
confirms a completed installation for them - both list them only in 2016's
"additional projects currently under contract" table, with just an
"expected" year, not a confirmed one:

- DeKalb/Peachtree (PDK): expected 2016. This project is *already* well
  documented in this database from earlier research (Dec 2018 completion,
  1,746 blocks, $8M - see docs/utreding_status_flygplatser.md) - better,
  more specific data than this fact sheet offers. No new row; a note is
  appended to the existing Installation cross-referencing both.
- Venice (VNC): expected 2016. Per instruction, cross-referenced against
  the Local10 News article mentioned in docs/utreding_status_flygplatser.md
  ("Ny kandidat" section) as independent confirmation VNC has EMAS. No new
  row (VNC already had one, contrary to that doc's assumption it might not)
  - a note is appended instead.
- Boca Raton (BCT): expected 2016. Already known from earlier research
  ("efter 2012", matches a Sept 2025 incident) but without a specific year
  on the Installation row - this fact sheet's "expected 2016" is the most
  specific date available, but it's still a projection, not a confirmed
  completion, so it's recorded as a note rather than a hard install_year.

One footnote from the 2016 sheet is left unresolved: Chicago Midway's ESCO
EMAS row carries a "****" marker that isn't defined anywhere in that
sheet's footnote legend (which only defines (), *, **, ***, +) - flagged in
this script's docstring and in the report rather than guessed at.

One identity note: at the time this script was written, the airport this
database had stored as "Sandiford" (Louisville, KY) was, per independent
web search, almost certainly a misreading of "Standiford" (Standiford
Field, the historic name of Louisville Muhammad Ali International,
IATA/ICAO/FAA SDF) - both fact sheets print "Sandiford" too, so this wasn't
a transcription error made here, but the underlying source's own apparent
typo. Left unrenamed at the time (out of scope for a data-only import) -
since fixed by scripts/rename_sandiford_to_standiford.py.

Safe to re-run: new Installation rows are guarded by (airport_id, type,
install_year), and each note-only append is guarded by checking whether the
2016 fact sheet's URL already appears in the target row's notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation
from scripts.annotate_signal import append_note
from scripts.import_faa_fact_sheets_2011_2016 import FACT_SHEET_2016_URL, get_or_create_2016_source


def find_airport(session: Session, code: str) -> Airport | None:
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


# (code, install_year, notes) - all sourced to the 2016 Fact Sheet.
AIRPORT_UPDATES = [
    ("JFK", 1996, (
        "2 system: A (1996, bädden ersatt 1999) och B (2007, bädden ersatt 2014), "
        "enligt 2016 Fact Sheet ('JFK International, Jamaica, NY, 2, 1996(1999)/2007(2014)'). "
        "Historiskt sett varldens forsta EMAS-installation (1996)."
    )),
    ("MSP", 1999, (
        "1 system, bädden ersatt 2008, enligt 2016 Fact Sheet ('Minneapolis-St. Paul, "
        "Minneapolis, MN, 1, 1999(2008)'). Loser den tidigare oppna fragan i "
        "docs/utredning_svaga_poster.md om okant installationsar for bana 12R."
    )),
    ("LIT", 2000, (
        "2 system (2000/2003), enligt bade 2011- och 2016-Fact Sheet, ingen andring."
    )),
    ("BUR", 2002, (
        "1 system, breddad 2008 ('*'), enligt bade 2011- och 2016-Fact Sheet, ingen "
        "andring. Matchar var egen tidigare research (BUR jan 2002, efter Southwest "
        "1455-olyckan mars 2000)."
    )),
    ("GMU", 2003, (
        "1 system, GA-flygplats ('**'), plus en retrofit 2010 ('***') enligt 2016 "
        "Fact Sheet ('Greenville Downtown, Greenville, SC, 1, 2003**/2010***') - 2011 "
        "visar bara grundaret. Detta ar en tidigare, separat retrofit an det egna "
        "2023-projektet (bana 19-ersattning/nyinstallation) som redan ar kant sedan "
        "tidigare."
    )),
    ("ROA", 2004, (
        "1 system, enligt bade 2011- och 2016-Fact Sheet, ingen andring. Matchar var "
        "egen tidigare research (2004 ursprunglig, helt ersatt 2024)."
    )),
    ("FLL", 2004, (
        "Vaxte fran 2 system (2011 Fact Sheet) till 4 (2016: 'Fort Lauderdale "
        "International, Fort Lauderdale, FL, 4, 2004, 2014') - tva ytterligare "
        "bäddar tillagda 2014. Detta ar skilt fran den senare, redan kanda "
        "oversvamningsskadan/ersattningen 2023-2025."
    )),
    ("LGA", 2005, (
        "Vaxte fran 2 system (2011) till 4 (2016: 'LaGuardia, Flushing, NY, 4, "
        "2005(2014)/2015') - ursprungssystemet ersatt 2014, ytterligare bäddar "
        "tillagda 2015."
    )),
    ("BOS", 2005, (
        "2 system: A (2005) och B (2006, ersatt bade 2012 och 2014 enligt 2016: "
        "'Boston Logan, Boston, MA, 2, 2005/2006(2012)(2014)'). Matchar var egen "
        "tidigare research (bana 33L/15R ombyggd till EMASMAX 2013 - nara nog samma "
        "tidsfonster). Ett tredje, helt nytt system under byggnation pa bana 27 "
        "(kant sedan tidigare) ligger efter bada dessa fact sheets."
    )),
    ("TEB", 2006, (
        "Reliever-flygplats ('+'). Vaxte fran 1 system (2011) till 3 (2016: "
        "'Teterboro, Teterboro, NJ, 3, 2006+/2011/2013') - matchar 2011 Fact "
        "Sheet's 'additional projects under contract'-post (forvantad sommaren "
        "2011) plus ytterligare ett system 2013."
    )),
    ("MDW", 2006, (
        "2 system (ESCO/EMASMAX-produktlinjen specifikt - halls isar fran "
        "Runway Safes greenEMAS-installationer pa samma flygplats, se "
        "scripts/add_gadelius_greenemas_installations.py), enligt 2016 Fact Sheet "
        "('Chicago Midway, Chicago, IL, 2, 2006/2007****'). OLOST: fotnoten '****' "
        "ar inte definierad nagonstans i dokumentets fotnotlegend (som bara "
        "definierar (), *, **, *** och +) - flaggat, inte gissat."
    )),
    ("CDV", 2007, (
        "Merle K (Mudhole) Smith, Cordova, AK. 1 system, enligt bade 2011- och "
        "2016-Fact Sheet, ingen andring."
    )),
    ("CRW", 2007, (
        "1 system, enligt bade 2011- och 2016-Fact Sheet, ingen andring. Matchar "
        "var egen tidigare research (2007, fanns redan vid 2010-olyckan)."
    )),
    ("AVP", 2008, (
        "Wilkes-Barre/Scranton Intl. 2 system, enligt bade 2011- och 2016-Fact "
        "Sheet, ingen andring."
    )),
    ("SBP", 2008, (
        "San Luis Obispo. 2 system, enligt bade 2011- och 2016-Fact Sheet, ingen "
        "andring."
    )),
    ("EWR", 2008, (
        "Vaxte fran 1 system (2011) till 2 (2016: 'Newark Liberty International, "
        "Newark, NJ, 2, 2008/2015')."
    )),
    ("STP", 2008, (
        "Reliever-flygplats ('+'). 2 system, enligt bade 2011- och 2016-Fact Sheet, "
        "ingen andring. Matchar var egen tidigare research (bada andar, DER 14/32)."
    )),
    ("ORH", 2008, (
        "2 system (2008/2009), GA-flygplats ('**'), enligt bade 2011- och "
        "2016-Fact Sheet, ingen andring. Detta ar den ursprungliga installationen - "
        "var egen tidigare research visar en senare, helt ny ersattning 2024/2025."
    )),
    ("RDG", 2009, (
        "GA-flygplats ('**'). 1 system, enligt bade 2011- och 2016-Fact Sheet, "
        "ingen andring. Matchar var egen tidigare research (sedan 2009)."
    )),
    ("MKC", 2009, (
        "Reliever-flygplats ('+') for forsta systemet. 2 system (2009/2010), enligt "
        "bade 2011- och 2016-Fact Sheet, ingen andring. Matchar var egen tidigare "
        "research (bada andar, DER 01/19)."
    )),
    ("EYW", 2010, (
        "Vaxte fran 1 system (2011) till 2 (2016: 'Key West International, Key "
        "West, FL, 2, 2010/2015'). Matchar var egen tidigare research (fanns redan "
        "nov 2011)."
    )),
    ("ACV", 2010, (
        "Arcata-Eureka. 1 system, enligt bade 2011- och 2016-Fact Sheet, ingen "
        "andring."
    )),
    ("TEX", 2010, (
        "Telluride Regional. 2 system, enligt bade 2011- och 2016-Fact Sheet, ingen "
        "andring. Matchar var egen tidigare research ('2009-2010')."
    )),
    ("LFT", 2011, (
        "Lafayette. 2 system (2011/2013) enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet. Ett tredje system namns dessutom i 2016:s "
        "'additional projects under contract'-tabell ('Lafayette, Lafayette, LA, 1, "
        "fall 2016') - forvantat, ej bekraftat fardigstallt av nagot dokument vi har."
    )),
    ("CLE", 2011, (
        "Cleveland Hopkins. 2 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("TTN", 2012, (
        "Trenton-Mercer. 4 system (2012/2013), enligt 2016 Fact Sheet - fanns inte "
        "alls i 2011-dokumentet."
    )),
    ("EWN", 2012, (
        "New Bern. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("MEM", 2013, (
        "Memphis. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("BKL", 2013, (
        "Burke Lakefront. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet. Matchar var egen tidigare research ('sedan ~2013')."
    )),
    ("SFO", 2014, (
        "4 system, enligt 2016 Fact Sheet - fanns inte alls i 2011-dokumentet. "
        "Matchar var egen tidigare research (4 baddar, sedan 2014)."
    )),
    ("PVD", 2014, (
        "T.F. Green. Forekommer i tva separata rader i 2016-dokumentet - "
        "'T.F. Green, Providence, RI, 1, 2014' och senare 'T. F. Green, Providence, "
        "RI, 1, fall 2015' - tolkat som 2 system totalt (2014 + fall 2015), i "
        "linje med hur andra flygplatser i samma dokument far en andra rad for ett "
        "tillagt system (jfr Chicago Executive nedan). Kan dock ocksa vara en "
        "dokumentationsdubblett - flaggat som osaker."
    )),
    ("ADS", 2014, (
        "Addison. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("PWK", 2014, (
        "Chicago Executive. Forekommer i tva separata rader i 2016-dokumentet - "
        "'Chicago Executive, Wheeling, IL, 1, 2014' och senare 'Chicago Exec, "
        "Wheeling, IL, 1, fall 2015' - tolkat som 2 system totalt (2014 + fall "
        "2015). Matchar var egen tidigare research (bada andar 16/34, ~2012-2015) "
        "rimligt val."
    )),
    ("DCA", 2014, (
        "Reagan National. 3 system (2014/2015), enligt 2016 Fact Sheet - fanns "
        "inte alls i 2011-dokumentet."
    )),
    ("MRY", 2015, (
        "Monterey. Forekommer i tva rader i 2016-dokumentet - 'Monterey, Monterey, "
        "CA, 1, 2015' och 'Monterey Regional, Monterey, CA, 1, fall 2015' - till "
        "skillnad fran T.F. Green/Chicago Executive (som gar fran ett tidigare "
        "arstal till 2015) anger bada Monterey-raderna samma ar (2015), sa detta "
        "tolkas har som EN dubblett-rad i kalldokumentet, inte tva system - "
        "install_year=2015, 1 system."
    )),
    ("OAK", 2015, (
        "Oakland International. 1 system, enligt 2016 Fact Sheet - fanns inte alls "
        "i 2011-dokumentet."
    )),
    ("OME", 2015, (
        "Nome, AK. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("ABE", 2015, (
        "Lehigh Valley. 2 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("JWN", 2015, (
        "John Tune, Nashville. 1 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("ADQ", 2015, (
        "Kodiak, AK. 2 system, enligt 2016 Fact Sheet - fanns inte alls i "
        "2011-dokumentet."
    )),
    ("RUT", 2015, (
        "Rutland, VT. 1 system, hosten 2015, enligt 2016 Fact Sheet - fanns inte "
        "alls i 2011-dokumentet."
    )),
    ("BDR", 2015, (
        "Sikorsky, Bridgeport, CT. 1 system, hosten 2015, enligt 2016 Fact Sheet - "
        "fanns inte alls i 2011-dokumentet."
    )),
    ("MFE", 2015, (
        "McAllen International. 1 system, hosten 2015, enligt 2016 Fact Sheet - "
        "fanns inte alls i 2011-dokumentet."
    )),
    ("SDF", 2015, (
        "1 system, hosten 2015, enligt 2016 Fact Sheet ('Sandiford, Louisville, "
        "KY, 1, fall 2015'). IDENTITETSANMARKNING (RATTAD): FAA:s kalldokument "
        "stavar fortfarande namnet 'Sandiford', men det ar med mycket stor "
        "sannolikhet en felstavning av 'Standiford' (Standiford Field, det "
        "historiska namnet pa Louisville Muhammad Ali International, IATA/ICAO/FAA "
        "SDF) - oberoende sokning bekraftar ett $18,8M Runway 11-29 Safety Area "
        "Improvement-projekt med EMAS, fardigstallt 'by late 2015', vilket matchar "
        "'fall 2015' nastan exakt. Flygplatsnamnet i var databas ar rattat till "
        "'Standiford' (scripts/rename_sandiford_to_standiford.py)."
    )),
]

# Airports with only an "expected" year from 2016's under-contract table -
# no confirmed completion in either fact sheet, so these get a note on the
# existing Installation rather than a new dated row.
UNDER_CONTRACT_ONLY_NOTES = {
    "PDK": (
        "2016 Fact Sheet listar detta under 'additional projects currently under "
        "contract' ('DeKalb/Peachtree, Atlanta, GA, 1, 2016') - bara en forvantan, "
        "inte en bekraftad fardigstallning. Var egen tidigare research "
        "(docs/utreding_status_flygplatser.md) har redan battre, mer specifik "
        "data: fardigstallt dec 2018, 1 746 block, 8 MUSD-projekt, Georgias forsta "
        "EMAS. Ingen ny Installation-rad skapad har - denna not lankar bara ihop "
        f"de tva kallorna. Kalla: {FACT_SHEET_2016_URL}"
    ),
    "VNC": (
        "2016 Fact Sheet listar detta under 'additional projects currently under "
        "contract' ('Venice, Venice, FL, 1, 2016') - bara en forvantan, inte en "
        "bekraftad fardigstallning. Oberoende bekraftelse: en Local10 News-artikel "
        "(feb 2026) om 'sex Florida-flygplatser med EMAS' namnger Venice (VNC) - "
        "se docs/utreding_status_flygplatser.md's 'Ny kandidat'-avsnitt (som antog "
        "VNC saknade Installation/Signal helt - det stammer inte, en generisk "
        "FAA-post fanns redan). Ingen ny Installation-rad skapad har (inget "
        "bekraftat installationsar att satta) - denna not lankar bara ihop "
        f"kallorna. Kalla: {FACT_SHEET_2016_URL}"
    ),
    "BCT": (
        "2016 Fact Sheet listar detta under 'additional projects currently under "
        "contract' ('Boca Raton, Boca Raton, FL, 1, 2016') - bara en forvantan, "
        "inte en bekraftad fardigstallning. Var egen tidigare research "
        "(docs/utreding_status_flygplatser.md) hade redan 'efter 2012' (Hog "
        "konfidens, matchar sept 2025-incidenten) men utan specifikt ar - detta ar "
        "den mest specifika dateringen som finns, men fortfarande en projektion, "
        "inte en oberoende bekraftad fardigstallning. Ingen ny Installation-rad "
        f"skapad har. Kalla: {FACT_SHEET_2016_URL}"
    ),
}


def seed(session: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"installations_created": 0, "under_contract_notes_added": 0}

    source_2016 = get_or_create_2016_source(session)

    for code, install_year, notes in AIRPORT_UPDATES:
        airport = find_airport(session, code)
        if airport is None:
            raise SystemExit(f"No airport with code={code}.")
        _, created = get_or_create_installation(
            session, airport=airport, install_year=install_year, source=source_2016, notes=notes,
        )
        if created:
            stats["installations_created"] += 1

    for code, note in UNDER_CONTRACT_ONLY_NOTES.items():
        airport = find_airport(session, code)
        if airport is None:
            raise SystemExit(f"No airport with code={code}.")
        installation = session.scalar(select(Installation).where(Installation.airport_id == airport.id))
        if installation is None:
            raise SystemExit(f"No Installation for {code}.")
        if FACT_SHEET_2016_URL not in (installation.notes or ""):
            installation.notes = append_note(installation.notes, note, on=today)
            stats["under_contract_notes_added"] += 1

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
