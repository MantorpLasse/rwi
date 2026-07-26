"""One-off enrichment of Blue Grass Airport's (LEX) EMAS Installation with a
confirmed source: Airport Improvement magazine's Nov 2021 coverage of LEX's
runway 4-22 rehabilitation project, which names EMAS's spring 2022
installation date, cost breakdown, funding, and vendor. Verified by fetching
https://airportimprovement.com/article/blue-grass-airport-races-repave-runway-72-hours/
and checking its figures against this script's data.

Resolves the open question from the 2026-07-26 investigation
(docs/utreding_status_flygplatser.md), which found EMAS existed at LEX but
could not confirm an install year (Medel confidence, "installationsår
okänt").

Repoints Installation.source_id at the Airport Improvement article
(Installation only holds one source_id; the old generic FAA-map source row
is left in place, just unlinked, per the pattern in
scripts/update_cgh_emas_details.py) and appends a note - the existing FAA
note is preserved rather than replaced, per the pattern in
scripts/add_zqn_wlg_official_source_confirmation.py.

Safe to re-run: the Airport Improvement Source is looked up by url before
creating a duplicate, and the note-append is guarded by checking whether
that url already appears in the installation's notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Source
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

AIRPORT_IMPROVEMENT_URL = "https://airportimprovement.com/article/blue-grass-airport-races-repave-runway-72-hours/"


def update_lex_installation(session: Session, *, today: date | None = None) -> tuple[Installation, bool]:
    today = today or date.today()

    lex = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "LEX", Airport.icao_code == "LEX", Airport.faa_code == "LEX"))
    )
    if lex is None:
        raise SystemExit("No airport with code=LEX.")

    installation = session.scalar(select(Installation).where(Installation.airport_id == lex.id))
    if installation is None:
        raise SystemExit("No Installation for LEX.")

    source = get_or_create_source(
        session,
        url=AIRPORT_IMPROVEMENT_URL,
        title="Blue Grass Airport Races to Repave Runway in 72 Hours",
        source_type="news",
        publisher="Airport Improvement",
        published_date=date(2021, 11, 1),
        summary=(
            "Branschartikel om LEX:s banrenoveringsprojekt (bana 4-22): EMAS "
            "planerat till våren 2022, kostnad $4M av ett totalt $24,5M-projekt, "
            "finansierat via FAA AIP-bidrag ($24M) + flygplatsens kapitalmedel. "
            "Faktarutan namnger Runway Safe som 'EMAS Supplier'."
        ),
    )

    installation.source = source
    installation.install_year = 2022
    installation.confirmed_vendor = "Runway Safe"

    if AIRPORT_IMPROVEMENT_URL in (installation.notes or ""):
        session.commit()
        return installation, False

    note = (
        "Del av ett $24,5M banrenoveringsprojekt (bana 4-22), varav EMAS-delen "
        "kostade $4M. Finansierat via FAA AIP-bidrag ($24M) + flygplatsens "
        "kapitalmedel. Löser tidigare öppen fråga om okänt installationsår från "
        f"2026-07-26-utredningen. Källa: Airport Improvement, \"Blue Grass Airport "
        f"Races to Repave Runway in 72 Hours\" (nov 2021), {AIRPORT_IMPROVEMENT_URL}"
    )
    installation.notes = append_note(installation.notes, note, on=today)
    session.commit()
    session.refresh(installation)
    return installation, True


def main() -> None:
    with SessionLocal() as session:
        installation, updated = update_lex_installation(session)
    print(f"Installation {installation.id} updated={updated}, install_year={installation.install_year}, "
          f"confirmed_vendor={installation.confirmed_vendor}, source_id={installation.source_id}")


if __name__ == "__main__":
    main()
