"""One-off enrichment of Congonhas' (CGH) EMAS Installation with verified
detail: the ordem de serviço date, cost, funding source and arresting-bed
dimensions, sourced from the Feb 2021 gov.br press release (fetched and
checked against the text) plus Aeroflap's 2021-2022 construction coverage
(found via search; content not fetchable - blocked by the site - so cited
by title/URL only, not verified word-for-word).

Replaces the placeholder Installation.notes from scripts/add_brazil_expansion.py
with the fuller, better-sourced version, and repoints Installation.source_id
at the gov.br press release (Installation only holds one source_id; the old
Poder360 source row is left in place, just unlinked). The Aug 2021 gov.br
follow-up (confirming FNAC as funder) and the five Aeroflap articles are
cited as text in notes rather than as their own Source rows, for the same
one-source-per-row reason.

Safe to re-run: the gov.br Source is looked up by url before creating a
duplicate, and notes/source_id are simply overwritten to the same values.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Source

GOV_BR_FEB_2021_URL = (
    "https://www.gov.br/transportes/pt-br/assuntos/noticias/2021/2/"
    "investimento-federal-vai-tornar-congonhas-primeiro-aeroporto-da-america-latina-com-sistema-emas"
)
GOV_BR_AUG_2021_URL = (
    "https://www.gov.br/transportes/pt-br/assuntos/noticias/2021/8/"
    "sistema-emas-congonhas-tarcisio-seguranca-valor-leilao"
)
AEROFLAP_URLS = (
    "https://www.aeroflap.com.br/congonhas-tera-nova-tecnologia-para-frear-avioes/",
    "https://www.aeroflap.com.br/infraero-ja-esta-fazendo-obras-para-implementar-o-emas-em-congonhas/",
    "https://www.aeroflap.com.br/com-concretagem-obras-do-emas-no-aeroporto-de-congonhas-estao-quase-finalizadas/",
    "https://www.aeroflap.com.br/obras-do-sistema-emas-sao-concluidas-no-aeroporto-de-congonhas-sp/",
    "https://www.aeroflap.com.br/sistema-emas-e-entregue-no-aeroporto-e-congonhas-sp-dois-meses-antes-do-prazo/",
)

INSTALLATION_NOTES = (
    "Latinamerikas första EMAS. OS undertecknad 2021-02-11, klar mars 2022. "
    "Båda ändar av bana 17R/35L (ca 70×45m / 75×45m). Kostnad R$122,5M via "
    "FNAC. Föranledd av 2007-olyckan (199 omkomna).\n\n"
    "Ytterligare källor: gov.br, \"Sistema EMAS Congonhas: Tarcísio, segurança, "
    "valor do leilão\" (2021-08, bekräftar FNAC-finansiering), "
    f"{GOV_BR_AUG_2021_URL}\n"
    "Aeroflap (2021-2022, byggnadsförlopp, ej textverifierat - sajten blockerade "
    "automatisk hämtning):\n"
    + "\n".join(f"- {url}" for url in AEROFLAP_URLS)
)


def get_or_create_source(session: Session, *, url: str, **fields) -> Source:
    source = session.scalar(select(Source).where(Source.url == url))
    if source is not None:
        return source
    source = Source(url=url, **fields)
    session.add(source)
    session.flush()
    return source


def update_cgh_installation(session: Session) -> Installation:
    cgh = session.scalar(select(Airport).where(Airport.iata_code == "CGH"))
    if cgh is None:
        raise SystemExit("No airport with iata_code=CGH - run scripts/add_brazil_expansion.py first.")
    installation = session.scalar(
        select(Installation).where(Installation.airport_id == cgh.id, Installation.install_year == 2022)
    )
    if installation is None:
        raise SystemExit("No 2022 Installation for CGH - run scripts/add_brazil_expansion.py first.")

    gov_br_source = get_or_create_source(
        session,
        url=GOV_BR_FEB_2021_URL,
        title="Investimento federal vai tornar Congonhas primeiro aeroporto da América Latina com sistema EMAS",
        source_type="news",
        publisher="Ministério da Infraestrutura",
        published_date=date(2021, 2, 11),
        summary=(
            "Official press release announcing the ordem de serviço signed 2021-02-11 "
            "for Congonhas' EMAS installation: R$122.5M investment, arresting beds at "
            "runway 17R (70x45m) and 35L (75x45m), 16-month execution window."
        ),
    )

    installation.source = gov_br_source
    installation.notes = INSTALLATION_NOTES
    session.commit()
    session.refresh(installation)
    return installation


def main() -> None:
    with SessionLocal() as session:
        installation = update_cgh_installation(session)
    print(f"Installation {installation.id} updated, source_id={installation.source_id}")


if __name__ == "__main__":
    main()
