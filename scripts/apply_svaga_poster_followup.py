"""Apply the two actionable DB changes recommended in
docs/utredning_svaga_poster.md (the BGM/MSP/VPC follow-up investigation).
ELM (Elmira-Corning) is checked but *not* touched: it already exists
(Airport id 48, with an EMASMAX Installation from the FAA map data) - the
instruction was to create it only if missing, and that condition wasn't met.

1. BGM (Greater Binghamton): creates a Source for the airport's own 2021
   Airport Master Plan Update (source_type=master_plan), which independently
   confirms the "Runway 16 EMAS" project's phased budget (Design $500K
   2021-22, Construction Phase I $7.425M + Phase II $3M, 2023-2028).

   - Signal 6 ("Runway 16 departure EMAS project") is repointed from its
     current source (a Broome County Capital Improvements Program page,
     more generic) to the AMPU (the airport's own document, with a specific
     phased budget) - the old source is left in place, just unlinked, per
     the pattern in scripts/update_cgh_emas_details.py. target_year is set
     to 2028 (the AMPU's Construction Phase II end date). A note records the
     old source and cross-references the USAspending grant signals.
   - The five USAspending grant Signals (59, 49, 55, 58, 60) keep their own
     source_id (each is the single most specific citation - the actual
     USAspending award page for that grant) - source_id is intentionally
     *not* replaced, per the "one source_id per row, cite the rest in notes"
     convention. Each gets a note appended cross-referencing which AMPU
     budget line its amount corresponds to.

2. VPC (Cartersville): appends a note to the existing Installation (id 23)
   downgrading the "EMAS at both runway ends" claim to Låg confidence - the
   follow-up investigation could not trace that specific claim to a
   verifiable primary source. install_year is left untouched (it was already
   None; the 2021 date lives only in this note and in
   docs/utredning_svaga_poster.md, not in a structured field, since it's
   still not confirmed against a primary source at the record level).

Safe to re-run: the AMPU Source is looked up by url before creating a
duplicate, and each note-append is guarded by checking whether the AMPU url
already appears in the target row's notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation, Signal, Source
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

BGM_AMPU_URL = "https://binghamtonairport.com/wp-content/uploads/2023/01/8-BGM-AMPU-Financial-Feasibility.pdf"

BGM_AMPU_SUMMARY = (
    "Greater Binghamton Airports egen 2021 Airport Master Plan Update (kapitel 8, "
    "Financial Feasibility, tabell 8-1; underlag McFarland Johnson 2021). Listar "
    "'Runway 16 EMAS' som ett fasat kapitalprojekt: Design $500K (2021-2022, varav "
    "$450K FAA-andel), Construction Phase I $7,425M (varav $6,683M FAA-andel) och "
    "Phase II $3,0M (varav $2,7M FAA-andel), båda 2023-2028. Totalt ca $10,9M. "
    "Finansiering 90% FAA / 5% delstat / 5% lokalt."
)

BGM_USASPENDING_MATCH = {
    59: (
        "AMPU:s 'Runway 16 EMAS – Design' ($500K totalt, $450K FAA-andel, 2021-2022) "
        "- FY2021-beloppet ($481K) ligger mycket nära denna post."
    ),
    49: (
        "AMPU:s 'Runway 16 EMAS – Construction Phase I' ($7,425M totalt, $6,683M "
        "FAA-andel, 2023-2028) - detta FY2023-belopp är den största av tre FY2023-poster "
        "som tillsammans ($5,4M+$1,6M+$1,0M=$8,0M) ligger nära Phase I:s totalkostnad."
    ),
    55: (
        "AMPU:s 'Runway 16 EMAS – Construction Phase I' ($7,425M totalt, $6,683M "
        "FAA-andel, 2023-2028) - del av samma FY2023-tranche som id 49 och 58 "
        "(tillsammans $8,0M, nära Phase I:s totalkostnad)."
    ),
    58: (
        "AMPU:s 'Runway 16 EMAS – Construction Phase I' ($7,425M totalt, $6,683M "
        "FAA-andel, 2023-2028) - del av samma FY2023-tranche som id 49 och 55 "
        "(tillsammans $8,0M, nära Phase I:s totalkostnad)."
    ),
    60: (
        "AMPU:s 'Runway 16 EMAS – Construction Phase II' ($3,0M totalt, $2,7M "
        "FAA-andel, 2023-2028) - detta FY2026-belopp ($415K) är en delpost, "
        "rimlig som en tidig/sen tranche inom Phase II."
    ),
}

VPC_NOTE = (
    "Uppföljning (docs/utredning_svaga_poster.md): påståendet om EMAS vid båda "
    "banändar kunde inte spåras till en verifierbar primärkälla. Två "
    "projektsidor (C.W. Matthews, entreprenör; Croy Engineering, "
    "projekterande ingenjörsfirma) bekräftar att en EMAS-installation ingick "
    "i 2021 års banrenovering, men ingen av dem anger explicit vilken/vilka "
    "banändar. Konfidensen för 'båda ändar' sänks därför till Låg tills en "
    "primärkälla (FAA Chart Supplement, NOTAM, eller flygplatsens egen Master "
    "Plan) hittas som anger runway ends explicit. Installationsåret 2021 "
    "kvarstår obekräftat på post-nivå (install_year ej satt)."
)


def get_or_create_master_plan_source(session: Session) -> Source:
    return get_or_create_source(
        session,
        url=BGM_AMPU_URL,
        title="Greater Binghamton Airport Master Plan Update — Financial Feasibility (Ch. 8)",
        source_type="master_plan",
        publisher="Greater Binghamton Airport",
        retrieved_at=date(2026, 7, 27),
        summary=BGM_AMPU_SUMMARY,
    )


def update_bgm_signals(session: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"signal_6_updated": False, "grant_signals_updated": 0}

    ampu_source = get_or_create_master_plan_source(session)

    signal_6 = session.get(Signal, 6)
    if signal_6 is None:
        raise SystemExit("No Signal id=6 (Runway 16 departure EMAS project).")
    if BGM_AMPU_URL not in (signal_6.notes or ""):
        old_source = session.get(Source, signal_6.source_id) if signal_6.source_id else None
        old_source_note = (
            f"Tidigare källa ({old_source.title!r}, {old_source.url}) kvar i databasen, "
            "men avlänkad - AMPU:n är en mer specifik, officiell primärkälla med "
            "fasindelad budget."
            if old_source is not None
            else "Ingen tidigare källa fanns."
        )
        note = (
            "Bekräftat via Greater Binghamton Airports egen 2021 Airport Master Plan "
            f"Update ({BGM_AMPU_URL}): fasindelat 'Runway 16 EMAS'-projekt, Design "
            "$500K (2021-2022), Construction Phase I $7,425M + Phase II $3,0M "
            "(2023-2028), totalt ca $10,9M. Matchar väl de fem "
            "USAspending-bidragssignalerna (id 59, 49, 55, 58, 60) - se deras egna "
            f"noter för detaljerad avstämning. {old_source_note}"
        )
        signal_6.source = ampu_source
        signal_6.target_year = 2028
        signal_6.notes = append_note(signal_6.notes, note, on=today)
        stats["signal_6_updated"] = True

    for signal_id, match_note in BGM_USASPENDING_MATCH.items():
        signal = session.get(Signal, signal_id)
        if signal is None:
            raise SystemExit(f"No Signal id={signal_id}.")
        if BGM_AMPU_URL in (signal.notes or ""):
            continue
        note = (
            f"Korsreferens (docs/utredning_svaga_poster.md): motsvarar troligen "
            f"{match_note} Källa: {BGM_AMPU_URL}"
        )
        signal.notes = append_note(signal.notes, note, on=today)
        stats["grant_signals_updated"] += 1

    session.commit()
    return stats


def downgrade_vpc_installation(session: Session, *, today: date | None = None) -> tuple[Installation, bool]:
    today = today or date.today()
    installation = session.get(Installation, 23)
    if installation is None:
        raise SystemExit("No Installation id=23 (VPC).")

    if "Uppföljning (docs/utredning_svaga_poster.md)" in (installation.notes or ""):
        return installation, False

    installation.notes = append_note(installation.notes, VPC_NOTE, on=today)
    session.commit()
    session.refresh(installation)
    return installation, True


def seed(session: Session, *, today: date | None = None) -> dict:
    stats = update_bgm_signals(session, today=today)
    _, vpc_updated = downgrade_vpc_installation(session, today=today)
    stats["vpc_updated"] = vpc_updated
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
