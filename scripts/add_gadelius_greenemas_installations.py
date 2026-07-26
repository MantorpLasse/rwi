"""One-off data entry for Runway Safe's greenEMAS installations as listed on
Gadelius' (RW's Japanese license partner) official product page - a single
canonical installation list covering multiple airports/years, several not
previously in this database. Verified by fetching
https://www.gadelius.com/products/license_engineering/greenemas_e.html and
checking its per-year entries against this script's data.

Adds new Installation rows (all type=greenEMAS, confirmed_vendor=
"Runway Safe", sourced from the Gadelius list unless noted otherwise):

- Chicago Midway (MDW, existing airport): a *new*, separate Installation
  row alongside the existing generic FAA-sourced greenEMAS row (id 26) -
  that row is left untouched per instruction, since it aggregates FAA map
  data rather than confirming a specific install year/vendor. This new row
  carries the Gadelius-confirmed install_year=2014 plus detail from the
  PRWeb press release already found in earlier research: first bed Nov
  2014 on runway 22L, four beds promised by end of 2016.
- Zurich International (ZRH, new airport, Switzerland), 2016.
- Roland Garros (RUN, new airport, Réunion/France), 2017.
- Dzaoudzi Pamandzi International (DZA, new airport, Mayotte/France), 2018
  - a French overseas region, not Madagascar (an earlier research pass had
  assumed the latter for what was probably this same airport).
- Saarbrücken (SCN, new airport, Germany), 2019.
- Tokyo Haneda International (HND, new airport, Japan), Sept 2019.
- RAF Northolt (NHT, new airport, UK), Oct 2019 - a military/business
  airport, not commercial.

Also:
- Chicago O'Hare (ORD, existing airport): not on Gadelius' list at all, but
  the same PRWeb release covering MDW's greenEMAS rollout says O'Hare got
  one "shortly after" - added as its own new Installation row (source=
  PRWeb, install_year left unset since the release gives no date),
  alongside the existing generic FAA-sourced EMASMAX row (id 27).
- Congonhas (CGH): Gadelius' list independently confirms Aug 2022, matching
  the existing Installation's install_year=2022 - corrects its `type` from
  the generic "EMAS" to "greenEMAS" and appends a note resolving the
  earlier open question about SDU's (planned) EMASMAX vs CGH's greenEMAS
  both potentially being "firsts": different product lines, no
  contradiction.

Safe to re-run: airports/sources are looked up before creating a
duplicate; new Installation rows are guarded by (airport_id, type,
install_year); CGH's note-append is guarded by checking whether the
Gadelius url already appears in its notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Source
from scripts.add_brazil_expansion import get_or_create_airport, get_or_create_source
from scripts.annotate_signal import append_note

GADELIUS_URL = "https://www.gadelius.com/products/license_engineering/greenemas_e.html"
PRWEB_URL = (
    "https://www.prweb.com/releases/chicago_airports_to_install_first_ever_sustainable_"
    "emas_solution_at_midway_and_o_hare/prweb12986556.htm"
)

GADELIUS_SUMMARY = (
    "Officiell produktsida (Runway Safe Groups japanska licenspartner Gadelius) med en "
    "årtalslista över greenEMAS-installationer: 2014 Chicago Midway, 2016 Zurich "
    "International, 2017 Roland Garros (Réunion), 2018 Dzaoudzi Pamandzi International "
    "(Mayotte), 2019 Saarbrücken, sep 2019 Tokyo Haneda International, okt 2019 Northolt "
    "(UK), aug 2022 CGH (Congonhas, Brasilien)."
)


def find_airport_by_code(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            or_(Airport.iata_code == code, Airport.icao_code == code, Airport.faa_code == code)
        )
    )


def get_or_create_installation(
    session: Session,
    *,
    airport: Airport,
    type_: str,
    install_year: int | None,
    **fields,
) -> tuple[Installation, bool]:
    existing = session.scalar(
        select(Installation).where(
            Installation.airport_id == airport.id,
            Installation.type == type_,
            Installation.install_year == install_year,
        )
    )
    if existing is not None:
        return existing, False
    installation = Installation(airport=airport, type=type_, install_year=install_year, **fields)
    session.add(installation)
    session.flush()
    return installation, True


def seed(session: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"airports_created": 0, "sources_created": 0, "installations_created": 0, "cgh_updated": False}

    def _airport(**kwargs):
        before = session.scalar(select(Airport).where(Airport.iata_code == kwargs["iata_code"]))
        airport = get_or_create_airport(session, **kwargs)
        if before is None:
            stats["airports_created"] += 1
        return airport

    def _source(**kwargs):
        before = session.scalar(select(Source).where(Source.url == kwargs["url"]))
        source = get_or_create_source(session, **kwargs)
        if before is None:
            stats["sources_created"] += 1
        return source

    gadelius = _source(
        url=GADELIUS_URL,
        title="Runway Safe Group AB greenEMAS System",
        source_type="news",
        publisher="Gadelius",
        retrieved_at=today,
        summary=GADELIUS_SUMMARY,
    )
    prweb = _source(
        url=PRWEB_URL,
        title="Chicago Airports to Install First Ever Sustainable EMAS Solution at Midway and O'Hare",
        source_type="news",
        publisher="PRWeb",
        published_date=date(2015, 6, 17),
        summary=(
            "Pressmeddelande om greenEMAS (återvunnet glas) på Chicago Midway: första "
            "bädden nov 2014 på bana 22L, totalt fyra bäddar utlovade till slutet av "
            "2016. Nämner att O'Hare fick greenEMAS 'shortly after' Midway, utan exakt "
            "datum."
        ),
    )

    mdw = find_airport_by_code(session, "MDW")
    if mdw is None:
        raise SystemExit("No airport with iata_code=MDW.")
    _, mdw_created = get_or_create_installation(
        session,
        airport=mdw,
        type_="greenEMAS",
        install_year=2014,
        source=gadelius,
        runway_end="22L",
        status="active",
        confirmed_vendor="Runway Safe",
        notes=(
            "Separat, nyare post utöver den befintliga generiska FAA-kartposten (id 26) "
            "för samma flygplats - den posten sammanfattar bara FAA:s kartdata utan "
            "bekräftat installationsår/leverantör, denna post bekräftar båda specifikt.\n\n"
            f"Installationsår 2014 bekräftat via Gadelius (RW:s japanska licenspartner) "
            f"officiella greenEMAS-produktsida ({GADELIUS_URL}).\n\n"
            "Detaljer enligt PRWeb-pressmeddelande "
            f"({PRWEB_URL}): första bädden klar nov 2014 på bana 22L, totalt fyra "
            "bäddar utlovade till slutet av 2016 (samma satsning som gav O'Hare "
            "greenEMAS 'shortly after')."
        ),
    )
    if mdw_created:
        stats["installations_created"] += 1

    ord_ = find_airport_by_code(session, "ORD")
    if ord_ is None:
        raise SystemExit("No airport with iata_code=ORD.")
    _, ord_created = get_or_create_installation(
        session,
        airport=ord_,
        type_="greenEMAS",
        install_year=None,
        source=prweb,
        status="active",
        confirmed_vendor="Runway Safe",
        notes=(
            "Separat, nyare post utöver den befintliga generiska FAA-kartposten (id 27, "
            "type=EMASMAX) för samma flygplats - den posten sammanfattar bara FAA:s "
            "kartdata, denna post bekräftar greenEMAS specifikt.\n\n"
            f"Ej på Gadelius ({GADELIUS_URL}) lista (som bara nämner Midway), men samma "
            f"PRWeb-pressmeddelande ({PRWEB_URL}) om Midways greenEMAS-utbyggnad säger "
            "att O'Hare fick greenEMAS 'shortly after' - inget exakt installationsår "
            "angivet, därför lämnat tomt."
        ),
    )
    if ord_created:
        stats["installations_created"] += 1

    new_airports = [
        dict(
            iata_code="ZRH",
            icao_code="LSZH",
            name="Zurich International Airport",
            city="Zurich",
            state_region="Zürich",
            country="Switzerland",
            install_year=2016,
        ),
        dict(
            iata_code="RUN",
            icao_code="FMEE",
            name="Roland Garros Airport",
            city="Saint-Denis",
            state_region="Réunion",
            country="France",
            install_year=2017,
        ),
        dict(
            iata_code="DZA",
            icao_code="FMCZ",
            name="Dzaoudzi Pamandzi International Airport",
            city="Dzaoudzi",
            state_region="Mayotte",
            country="France",
            install_year=2018,
            extra_note=(
                "Mayotte är en fransk utomeuropeisk region (departement) i Moçambique-"
                "kanalen, inte Madagaskar - troligen den flygplats som tidigare "
                "felaktigt antogs vara i Madagaskar."
            ),
        ),
        dict(
            iata_code="SCN",
            icao_code="EDDR",
            name="Saarbrücken Airport",
            city="Saarbrücken",
            state_region="Saarland",
            country="Germany",
            install_year=2019,
        ),
        dict(
            iata_code="HND",
            icao_code="RJTT",
            name="Tokyo Haneda International Airport",
            city="Tokyo",
            state_region="Ota, Tokyo",
            country="Japan",
            install_year=2019,
            extra_note="Enligt Gadelius klar sep 2019.",
        ),
        dict(
            iata_code="NHT",
            icao_code="EGWU",
            name="RAF Northolt",
            city="Ruislip",
            state_region="London",
            country="United Kingdom",
            install_year=2019,
            extra_note=(
                "Enligt Gadelius klar okt 2019. Militär/affärsflygplats (RAF Northolt), "
                "inte en kommersiell flygplats."
            ),
        ),
    ]

    for entry in new_airports:
        extra_note = entry.pop("extra_note", None)
        install_year = entry.pop("install_year")
        airport = _airport(**entry)
        notes = (
            f"greenEMAS installerad {install_year}, enligt Gadelius (RW:s japanska "
            f"licenspartner) officiella produktsida ({GADELIUS_URL})."
        )
        if extra_note:
            notes = f"{notes} {extra_note}"
        _, created = get_or_create_installation(
            session,
            airport=airport,
            type_="greenEMAS",
            install_year=install_year,
            source=gadelius,
            status="active",
            confirmed_vendor="Runway Safe",
            notes=notes,
        )
        if created:
            stats["installations_created"] += 1

    cgh = find_airport_by_code(session, "CGH")
    if cgh is None:
        raise SystemExit("No airport with iata_code=CGH - run scripts/add_brazil_expansion.py first.")
    cgh_installation = session.scalar(
        select(Installation).where(Installation.airport_id == cgh.id, Installation.install_year == 2022)
    )
    if cgh_installation is None:
        raise SystemExit("No 2022 Installation for CGH - run scripts/add_brazil_expansion.py first.")
    if cgh_installation.type != "greenEMAS":
        cgh_installation.type = "greenEMAS"
    if GADELIUS_URL not in (cgh_installation.notes or ""):
        note = (
            "Typ rättad till greenEMAS (var generisk 'EMAS'), bekräftat via Gadelius "
            f"(RW:s japanska licenspartner) officiella produktsida ({GADELIUS_URL}), "
            "som oberoende anger aug 2022 - matchar det redan registrerade "
            "install_year=2022. Löser tidigare öppen fråga om 'first EMAS' vs SDU:s "
            "'first EMASMAX' - Congonhas fick greenEMAS (annan produktlinje), SDU får "
            "EMASMAX. Ingen motsägelse."
        )
        cgh_installation.notes = append_note(cgh_installation.notes, note, on=today)
        stats["cgh_updated"] = True

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
