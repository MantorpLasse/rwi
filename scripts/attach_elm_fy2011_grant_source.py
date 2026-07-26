"""Complement ELM's (Elmira-Corning Regional) existing EMAS Installation (id
43) with the FAA FY2011 AIP grant that most likely funded it, found while
following up on docs/utredning_svaga_poster.md's BGM research (same PDF,
fy2011-aip-grants.pdf, page 13).

Installation 43 currently cites only the generic FAA EMAS Incidents and
Installations map (source_type=faa_tableau) - authoritative on *where* the
bed is (runway end 06) but silent on cost, year, or project context. The
FY2011 AIP grant ($8,556,627: $1,413,499 entitlement + $7,143,128
discretionary, Grant Seq 56) is more specific on *why*/*when*: "Extend
Runway (401Ft) of Runway 24 and Parallel Taxiway A Including Purchase and
Installation of EMAS Blocks for RSA" - 06/24, funded in FY2011 (report dated
2011-11-30).

Neither source supersedes the other (one locates, one funds/dates), so this
*complements* rather than replaces: the existing source_id (FAA map) is left
untouched, and the grant is added as its own Source row, cited by url/text
in Installation.notes - the same "one source_id per row, cite the rest in
notes" pattern used throughout this codebase (see
scripts/update_cgh_emas_details.py, scripts/apply_svaga_poster_followup.py).
install_year is deliberately left unset: a grant *award* year doesn't
necessarily equal the physical completion year, and the FAA map source
doesn't independently confirm 2011 either.

Safe to re-run: the grant Source is looked up by url before creating a
duplicate, and the note-append is guarded by checking whether that url
already appears in the installation's notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

FAA_FY2011_GRANTS_URL = "https://www.faa.gov/sites/faa.gov/files/airports/aip/grant_histories/fy2011-aip-grants.pdf"

GRANT_SUMMARY = (
    "FAA AIP FY2011 Grants Awarded Report (rapportdatum 2011-11-30), rad for "
    "Elmira/Corning Regional (ELM), Grant Seq 56: AIP Federal Funds $8,556,627 "
    "($1,413,499 entitlement + $7,143,128 discretionary). Beskrivning: "
    "'Extend Runway [Construct Extension (401Ft) of Runway 24 and Parallel "
    "Taxiway A Including Purchase and Installation of EMAS Blocks for RSA] "
    "- 06/24'."
)

NOTE = (
    "Kompletterande källa (docs/utredning_svaga_poster.md, bifynd): FAA:s "
    "egen FY2011 AIP-bidragshistorik (Grant Seq 56, $8,556,627) namnger "
    "explicit inköp och installation av EMAS-block for RSA vid 06/24, "
    "bunt med en 401 ft banförlängning av bana 24. Troligen finansieringen "
    "bakom den redan bekräftade installationen (samma flygplats, samma "
    "bana 06/24) - men install_year sätts inte utifrån detta ensamt, "
    f"eftersom ett bidragsår inte nödvändigtvis är samma år som fysiskt "
    f"färdigställande. Källa: {FAA_FY2011_GRANTS_URL}"
)


def attach_grant_source(session: Session, *, today: date | None = None) -> tuple[Installation, bool]:
    today = today or date.today()

    installation = session.get(Installation, 43)
    if installation is None:
        raise SystemExit("No Installation id=43 (ELM).")

    grant_source = get_or_create_source(
        session,
        url=FAA_FY2011_GRANTS_URL,
        title="FAA FY2011 AIP Grants Awarded Report - Elmira/Corning Regional (Grant Seq 56)",
        source_type="aip_grant",
        publisher="FAA",
        retrieved_at=today,
        document_reference="Grant Seq 56, FY2011 Grants Awarded Report (2011-11-30)",
        summary=GRANT_SUMMARY,
    )

    if FAA_FY2011_GRANTS_URL in (installation.notes or ""):
        return installation, False

    installation.notes = append_note(installation.notes, NOTE, on=today)
    session.commit()
    session.refresh(installation)
    return installation, True


def main() -> None:
    with SessionLocal() as session:
        installation, updated = attach_grant_source(session)
    print(f"Installation {installation.id} updated={updated}, source_id unchanged={installation.source_id}")


if __name__ == "__main__":
    main()
