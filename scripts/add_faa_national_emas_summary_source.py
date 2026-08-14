"""Add a standalone, non-airport-specific Source row for the FAA's national
EMAS track record as of 2026-08-12: 26 arrestments total, 497 crew/
passengers saved, cited alongside Philadelphia Mayor Cherelle Parker's "117
systems total in the United States" quote (from the PHL Runway 8-26
completion event - see scripts/confirm_phl_emas_completion.py) as the
current national comparison point.

Deliberately not attached to any Signal/Installation/Incident via source_id
- Source rows normally get created alongside whatever cites them (see
app/models/source.py's docstring: "Referenced by source_id from the other
tables rather than owning them"), but this one is background/reference
material for the site as a whole, not evidence for one airport's row.

The FAA's own EMAS newsroom page (faa.gov/newsroom/engineered-material-
arresting-system-emas-0) returns 403 Forbidden to a direct fetch (FAA's site
appears to block non-browser user agents, same issue noted in
scripts/add_mdw_emas_bed_repairs_signal.py for chicago.gov). The 26/497
figures were corroborated via independent search-engine indexing of that
page's content and cross-checked against the user's own research.

Safe to re-run: the Source is looked up by url before creating a duplicate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from scripts.add_brazil_expansion import get_or_create_source

FAA_URL = "https://www.faa.gov/newsroom/engineered-material-arresting-system-emas-0"


def add_source(session: Session, *, today: date | None = None):
    today = today or date.today()
    source = get_or_create_source(
        session,
        url=FAA_URL,
        title="Engineered Material Arresting System (EMAS) - FAA national track record",
        source_type="faa_fact_sheet",
        publisher="FAA",
        published_date=date(2026, 8, 12),
        retrieved_at=today,
        summary=(
            "FAA:s nationella sammanstallning: EMAS har stoppat 26 overskjutande "
            "flygplan totalt och raddat 497 besattningsmedlemmar/passagerare. Citeras "
            "tillsammans med Philadelphia-borgmastare Cherelle Parkers uttalande (vid "
            "PHL:s Runway 8-26-invigning, se scripts/confirm_phl_emas_completion.py) om "
            "'117 system totalt i USA' som aktuell jamforelsepunkt. Direkthamtning av "
            "FAA-sidan gav 403 Forbidden (samma blockering som noterats for "
            "chicago.gov i scripts/add_mdw_emas_bed_repairs_signal.py) - siffrorna "
            "bekraftade via sokmotorindexering av sidans innehall."
        ),
    )
    session.commit()
    session.refresh(source)
    return source


def main() -> None:
    with SessionLocal() as session:
        source = add_source(session)
    print(f"Source {source.id} ({source.title!r}) url={source.url}")


if __name__ == "__main__":
    main()
