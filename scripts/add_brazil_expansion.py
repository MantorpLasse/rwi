"""One-off data entry for Runway Safe's first international expansion (see
README's "Nästa steg" / PLAN_FORENKLING.md's international-coverage section).

Adds two Brazilian airports sourced from Brazilian aviation-infra news
coverage of Ministério de Portos e Aeroportos' Santos Dumont investment
package:

- Congonhas (CGH): a confirmed Installation - EMAS installed 2022,
  following the 2007 runway excursion accident (199 deaths).
- Santos Dumont (SDU): a medium-confidence new_installation Signal - EMAS
  construction start date is ambiguous in the primary source (see notes).

Safe to re-run: airports are looked up by iata_code and sources by url
before creating a duplicate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Signal, Source

PODER360_URL = "https://www.poder360.com.br/poder-infra/santos-dumont-tera-investimento-de-r-400-milhoes-ate-2027/"
AGENCIA_INFRA_URL = (
    "https://agenciainfra.com/blog/obra-que-levou-a-reducao-da-capacidade-no-aeroporto-"
    "santos-dumont-tera-inicio-em-setembro-diz-ministerio/"
)

SDU_SIGNAL_NOTES = (
    "Del av R$400M-investeringspaket (2024-2027) från Ministério de Portos e "
    "Aeroportos. Kostnad för EMAS-delen specifikt: ca R$150M. Ordem de Serviço "
    "(byggstart) enligt artikel ursprungligen planerad september 2024, men artikeln "
    "uppdaterades 2026-03-13 utan att tydliggöra om 'september' syftar på ett nytt "
    "datum (möjligen sep 2026) eller är en ouppdaterad kvarleva. OBEKRÄFTAT - "
    "verifiera aktuell status innan hög confidence sätts."
)


def get_or_create_airport(session: Session, *, iata_code: str, icao_code: str, name: str, city: str, state_region: str, country: str) -> Airport:
    airport = session.scalar(select(Airport).where(Airport.iata_code == iata_code))
    if airport is not None:
        return airport
    airport = Airport(
        iata_code=iata_code,
        icao_code=icao_code,
        name=name,
        city=city,
        state_region=state_region,
        country=country,
    )
    session.add(airport)
    session.flush()
    return airport


def get_or_create_source(session: Session, *, url: str, **fields) -> Source:
    source = session.scalar(select(Source).where(Source.url == url))
    if source is not None:
        return source
    source = Source(url=url, **fields)
    session.add(source)
    session.flush()
    return source


def seed(session: Session) -> dict:
    stats = {"airports_created": 0, "sources_created": 0, "installations_created": 0, "signals_created": 0}

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

    cgh = _airport(
        iata_code="CGH",
        icao_code="SBSP",
        name="Congonhas Airport",
        city="São Paulo",
        state_region="São Paulo",
        country="Brazil",
    )
    sdu = _airport(
        iata_code="SDU",
        icao_code="SBRJ",
        name="Santos Dumont Airport",
        city="Rio de Janeiro",
        state_region="Rio de Janeiro",
        country="Brazil",
    )

    poder360 = _source(
        url=PODER360_URL,
        title="Santos Dumont terá investimento de R$ 400 milhões até 2027",
        source_type="news",
        publisher="Poder360",
        published_date=date(2024, 9, 2),
        summary=(
            "Confirms EMAS was installed at Congonhas in 2022, following the 2007 "
            "runway excursion accident (199 deaths); Congonhas was prioritized for "
            "EMAS due to its short runways. Also covers Santos Dumont's R$400M "
            "investment package through 2027 funded by Ministério de Portos e "
            "Aeroportos."
        ),
    )
    agencia_infra = _source(
        url=AGENCIA_INFRA_URL,
        title=(
            "Obra que levou à redução da capacidade no aeroporto Santos Dumont "
            "terá início em setembro, diz ministério"
        ),
        source_type="news",
        publisher="Agência iNFRA",
        published_date=date(2024, 8, 14),
        summary=(
            "Estimates the EMAS portion of Santos Dumont's investment at ~R$150M "
            "(within a broader ~R$270M package) and states the Ordem de Serviço "
            "(construction start) would be issued 'em setembro deste ano'. "
            "Originally published 2024-08-14, updated 2026-03-13 without "
            "clarifying which year's September the text now refers to."
        ),
    )

    existing_installation = session.scalar(
        select(Installation).where(Installation.airport_id == cgh.id, Installation.install_year == 2022)
    )
    if existing_installation is None:
        session.add(
            Installation(
                airport=cgh,
                source=poder360,
                type="EMAS",
                install_year=2022,
                status="active",
                notes=(
                    "Installerades 2022, efter banurspårningsolyckan 2007 (199 "
                    "omkomna). Congonhas prioriterades för EMAS på grund av sina "
                    "korta banor."
                ),
            )
        )
        stats["installations_created"] += 1

    existing_signal = session.scalar(
        select(Signal).where(Signal.airport_id == sdu.id, Signal.category == "new_installation")
    )
    if existing_signal is None:
        session.add(
            Signal(
                airport=sdu,
                source=agencia_infra,
                title="Santos Dumont EMAS installation (R$400M investment package)",
                category="new_installation",
                confidence="medium",
                notes=(
                    SDU_SIGNAL_NOTES
                    + "\n\nYtterligare källa: Poder360, "
                    + '"Santos Dumont terá investimento de R$ 400 milhões até 2027" '
                    + f"(2024-09-02), {PODER360_URL}"
                ),
            )
        )
        stats["signals_created"] += 1

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
