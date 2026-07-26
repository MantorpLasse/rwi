"""Implements the suggestions from docs/utreding_faa_tableau.md: adds a
dated, sourced Installation row for each of the six `faa_tableau`-only
airports where a hard install year could be found or was already known
from earlier research but never backfilled into `install_year`.

- OXC (Waterbury-Oxford): 2018, sourced to a SimpleFlying aggregator
  article (Medel confidence - no primary/cost source found; CT Airport
  Authority's own Master Plan Update PDF returned HTTP 403).
- CGF (Cuyahoga County): 2018, sourced to Cuyahoga County's own press
  release. Two rows (not one) - the EMAS beds at each end of runway 6/24
  have distinct, specifically-reported lengths (322 ft at 6, 435 ft at
  24), the same "one row per runway end" pattern already used for
  STP/MKC's generic FAA-map rows.
- HXD (Hilton Head): 2018, sourced to an Airport Improvement Magazine
  article ("late June 2018") - a second, uncited aggregator claims 2019;
  that discrepancy is recorded in notes as unresolved, not silently
  dropped.
- PHL (Philadelphia Intl): 2025, sourced to the airport's own newsroom
  page (phl.org) - the most authoritative of the three articles docs/
  utreding_status_flygplatser.md had already cited (PHL Airport, 6abc,
  AirlineGeeks). confirmed_vendor="Runway Safe" per instruction.
- PDK (DeKalb-Peachtree): 2018, sourced to the Airport Improvement
  Magazine article already cited in earlier research (docs/
  utreding_status_flygplatser.md's "Airport Improvement magazine, AJC,
  DeKalb County"). confirmed_vendor="Zodiac Aerospace" (the article
  names Zodiac, not Runway Safe/ESCO - Zodiac's EMAS business exited the
  US market in 2018 after merging into Safran, per the same article).
- BCT (Boca Raton): 2016 (start of the airport's own stated "2016-2017"
  timeline), sourced to the airport's own EMAS project page
  (bocaairport.com) - fetched directly since WebFetch's HTML->markdown
  conversion truncated this page to just its nav menu; the raw HTML
  contains "Budget $12M" / "Timeline 2016-2017" and confirms both ends of
  runway 5/23, vendor "Originally ESCO, now Runway Safe Inc."
  confirmed_vendor="Runway Safe" accordingly.

VNC (Venice) is deliberately **not** touched - per instruction, no hard
year was found (only scope/size details from a contractor's project page
and a page-generation date that doesn't confirm project completion), so
docs/utreding_faa_tableau.md's suggestion to leave it as-is applies.

Every one of these airports already has a generic, year-less
`faa_tableau`-sourced Installation row - this script adds a *new*,
separate row per airport (or two, for CGF), leaving the old one
untouched, exactly as every previous FAA-fact-sheet/Gadelius import in
this codebase has done.

Safe to re-run: Sources are looked up by url before creating a
duplicate, and new Installation rows are guarded by (airport_id, type,
install_year, runway_end).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Source
from scripts.add_brazil_expansion import get_or_create_source

SIMPLEFLYING_URL = "https://simpleflying.com/emas-airports-guide/"
CUYAHOGA_URL = (
    "https://cuyahogacounty.gov/executive/news/press-releases-archive/2019-press-releases/2020/11/10/"
    "cuyahoga-county-airport-celebrates-completion-of-$39-million-runway-safety-area-improvement-project"
)
HXD_URL = (
    "https://airportimprovement.com/article/"
    "runway-improvements-hilton-head-airport-enhance-safety-service-options-stormwater-management/"
)
PHL_URL = "https://www.phl.org/newsroom/EMAS"
PDK_URL = "https://airportimprovement.com/article/new-arrestor-bed-dekalb-peachtree-signals-industry-change/"
BCT_URL = "https://bocaairport.com/portfolio-items/engineered-materials-arresting-system-emas/"


def find_airport(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(or_(Airport.iata_code == code, Airport.icao_code == code, Airport.faa_code == code))
    )


def get_or_create_installation(
    session: Session, *, airport: Airport, install_year: int, runway_end: str | None = None, **fields
) -> tuple[Installation, bool]:
    existing = session.scalar(
        select(Installation).where(
            Installation.airport_id == airport.id,
            Installation.type == "EMASMAX",
            Installation.install_year == install_year,
            Installation.runway_end == runway_end,
        )
    )
    if existing is not None:
        return existing, False
    installation = Installation(
        airport=airport, type="EMASMAX", install_year=install_year, runway_end=runway_end,
        status="active", **fields,
    )
    session.add(installation)
    session.flush()
    return installation, True


def seed(session: Session) -> dict:
    stats = {"sources_created": 0, "installations_created": 0}

    def _source(**kwargs):
        before = session.scalar(select(Source).where(Source.url == kwargs["url"]))
        source = get_or_create_source(session, **kwargs)
        if before is None:
            stats["sources_created"] += 1
        return source

    def _install(code, **kwargs):
        airport = find_airport(session, code)
        if airport is None:
            raise SystemExit(f"No airport with code={code}.")
        _, created = get_or_create_installation(session, airport=airport, **kwargs)
        if created:
            stats["installations_created"] += 1

    simpleflying = _source(
        url=SIMPLEFLYING_URL,
        title="EMAS: 5 Things To Know About Engineered Materials Arresting Systems In Aviation",
        source_type="news",
        publisher="Simple Flying",
        summary=(
            "Oversiktsartikel (aggregator) som daterar Waterbury-Oxfords EMAS till 2018. Ingen "
            "kostnad, leverantor eller banande angiven - Medel konfidens, ingen starkare kalla "
            "hittad (CT Airport Authoritys egen AMPU-PDF gav HTTP 403)."
        ),
    )
    _install(
        "OXC", install_year=2018, source=simpleflying,
        notes=(
            "Installerades vinter 2017/2018 (arbete pagick 'end of November 2017 to beginning of "
            "March 2018' enligt sammanstallningsartikel). Konfidens: Medel - ingen kostnad, "
            "leverantor eller banande hittad, och CT Airport Authoritys egen Master Plan Update "
            "(ctairports.org/wp-content/uploads/2017/05/finalAMPU.pdf) gick inte att hamta "
            "(HTTP 403). Se docs/utreding_faa_tableau.md."
        ),
    )

    cuyahoga = _source(
        url=CUYAHOGA_URL,
        title="Cuyahoga County Airport Celebrates Completion of $39 Million Runway Safety Area Improvement Project",
        source_type="news",
        publisher="Cuyahoga County",
        published_date=date(2020, 11, 10),
        summary=(
            "Pressmeddelande om hela det fyrfasiga $39M RSA-forbattringsprojektet (2016-2020). "
            "EMAS-delen specifikt (Faserna 3-4) fardigstalldes 2018: tva baddar, en vid varje "
            "banande av bana 6/24 (322 fot vid bana 6, 435 fot vid bana 24), tillsammans med en "
            "511-fots forlangning av bana 6, ny taxibana, inflygningsljus och AWOS. Finansierat "
            "via FAA, ODOT (Ohio DOT Office of Aviation) och countyt."
        ),
    )
    _install(
        "CGF", install_year=2018, runway_end="06", source=cuyahoga,
        length_m=98.1,  # 322 ft
        notes=(
            "EMAS-badd vid bana 6:s avgangsande, 322 fot lang, del av Fas 3-4 av ett fyrfasigt "
            "$39M Runway Safety Area Improvement Project (2016-2020, hela projektet firat klart "
            "nov 2020, men EMAS-delen specifikt fardigstalld 2018). Se docs/utreding_faa_tableau.md."
        ),
    )
    _install(
        "CGF", install_year=2018, runway_end="24", source=cuyahoga,
        length_m=132.6,  # 435 ft
        notes=(
            "EMAS-badd vid bana 24:s avgangsande, 435 fot lang, del av samma Fas 3-4-projekt som "
            "bana 6-badden (se den raden for projektdetaljer). Se docs/utreding_faa_tableau.md."
        ),
    )

    hxd_source = _source(
        url=HXD_URL,
        title="Runway Improvements at Hilton Head Airport Enhance Safety, Service Options & Stormwater Management",
        source_type="news",
        publisher="Airport Improvement Magazine",
        summary=(
            "Branschartikel: tva EMAS-baddar (200 fot vardera), en vid varje banande, del av ett "
            "$8M projekt (90% FAA, 5% South Carolina Aeronautics Commission, resten "
            "flygplatsintakter). ~18 manaders projekt, klart 'slutet av juni 2018'. "
            "Huvudentreprenor Quality Enterprises USA (specifik EMAS-leverantor ej namngiven)."
        ),
    )
    _install(
        "HXD", install_year=2018, source=hxd_source,
        notes=(
            "2 baddar (banande 21: 211x105 fot, banande 03: 207x105 fot enligt FAA-kartdata), "
            "$8M projekt (90% FAA/5% SC Aeronautics Commission/resten flygplatsintakter), ~18 "
            "manaders byggtid, klart enligt Airport Improvement Magazine 'slutet av juni 2018'. "
            "OLOST DISKREPANS: en annan, svagare (ospecificerad aggregator-)kalla anger istallet "
            "2019 - inte verifierad mot nagon primarkalla, darfor inte anvand har. Se "
            "docs/utreding_faa_tableau.md."
        ),
    )

    phl_source = _source(
        url=PHL_URL,
        title="Philadelphia International Airport Completes Runway Safety Project",
        source_type="news",
        publisher="Philadelphia International Airport",
        published_date=date(2025, 8, 26),
        summary=(
            "Flygplatsens egen officiella sida: EMAS pa bana 8-26 (ostra sidan), fardigstallt "
            "2025-06-12, 2184 crushable cellular concrete-block, 211 fot langd, 389 fot fran "
            "banandan. Kostnad $8 547 648 (FAA Airport Infrastructure Grant). Generalentreprenor "
            "James J. Anderson Construction Company (JJA)."
        ),
    )
    _install(
        "PHL", install_year=2025, source=phl_source, confirmed_vendor="Runway Safe",
        notes=(
            "PHL:s forsta EMAS nagonsin, fardigstallt 2025-06-12, ost om bana 8-26, ~2184 "
            "EMASMAX-block (211 fot langd, 389 fot fran banandan), $8 547 648 (FAA Airport "
            "Infrastructure Grant) - matchar den redan befintliga USAspending-signalen (id 43, "
            "$8,5M FY2024). Generalentreprenor JJA Construction. Se "
            "docs/utreding_status_flygplatser.md och docs/utreding_faa_tableau.md."
        ),
    )

    pdk_source = _source(
        url=PDK_URL,
        title="New Arrestor Bed at DeKalb-Peachtree Signals Industry Change",
        source_type="news",
        publisher="Airport Improvement Magazine",
        summary=(
            "Branschartikel: EMAS-badd fardigstalld dec 2018 (slutgradning apr 2019), 1746 "
            "block, $8M for hela RSA-projektet varav $2,5M for sjalva EMAS-badden, 90% federalt/"
            "5% delstat/5% flygplats. Leverantor Zodiac Aerospace (franskt bolag - lamnade den "
            "amerikanska marknaden 2018 efter Safran-sammanslagningen)."
        ),
    )
    _install(
        "PDK", install_year=2018, source=pdk_source, confirmed_vendor="Zodiac Aerospace",
        notes=(
            "Georgias forsta EMAS, katalyserad av en Beechcraft-incident 2012. Fardigstalld dec "
            "2018 (slutgradning apr 2019), 1746 block, $8M totalt varav $2,5M for EMAS-badden "
            "specifikt (90% federalt/5% delstat/5% flygplats). Leverantor Zodiac Aerospace - "
            "lamnade den amerikanska EMAS-marknaden 2018 efter sammanslagningen med Safran. "
            "Matchar den redan befintliga incident-signalen (id 22). Se "
            "docs/utreding_status_flygplatser.md och docs/utreding_faa_tableau.md."
        ),
    )

    bct_source = _source(
        url=BCT_URL,
        title="Engineered Materials Arresting System",
        source_type="news",
        publisher="Boca Raton Airport Authority",
        summary=(
            "Flygplatsmyndighetens egen sida (rafetchad direkt, WebFetchs HTML->markdown-"
            "konvertering trunkerade sidan till bara navigeringsmenyn): 'Budget $12M', "
            "'Timeline 2016-2017'. Baddar vid bada andar av bana 5/23. Foranlett av en 2012 "
            "Operational Needs Assessment/RSA-studie som konstaterade EMAS som enda praktiska "
            "alternativ (Spanish River Blvd. och en el-anlaggning begransar ytan). Leverantor "
            "'Originally ESCO, now Runway Safe Inc.'"
        ),
    )
    _install(
        "BCT", install_year=2016, source=bct_source, confirmed_vendor="Runway Safe",
        notes=(
            "Baddar vid bada andar av bana 5/23, byggda enligt flygplatsens egen sidas "
            "'Timeline 2016-2017' (2016 anvant som install_year - forsta delen av tidslinjen; "
            "hela projektet strackte sig in i 2017). Budget $12M. Foranlett av en 2012 RSA-"
            "studie (Spanish River Blvd. och en el-anlaggning gor en full 1000-fots RSA "
            "ogenomforbar). Leverantor: 'Originally ESCO, now Runway Safe Inc.' enligt "
            "flygplatsens egen sida. Uppgraderar tidigare 'efter 2012'-uppskattning till ett "
            "konkret arstal. Se docs/utreding_faa_tableau.md."
        ),
    )

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
