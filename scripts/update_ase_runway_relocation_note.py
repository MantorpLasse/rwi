"""Add newly published Aspen/Pitkin County (ASE) runway-relocation detail to
the Runway 15/33 EMAS Signal's source_notes, without resolving the existing
uncertainty about whether EMAS is actually in the project's scope.

The August 2026 sources (aspenairport.com/construction, Pitkin County's
2026-05-28 update, and SimpleFlying's 2026-06-01 article) describe a ~$575M
runway modernization program - 80 feet of westward relocation, widening from
100 to 150 feet for FAA's current Runway Design Code standards, construction
starting April 2027 - but none of them mention EMAS specifically. That is new
information *confirming* the prior EMAS-scope uncertainty still stands, not
information resolving it, so this note explicitly says so rather than
implying the tension is settled. Signal.confidence is deliberately left
unchanged ("planned", which app/static_export/build.py's _CONFIDENCE_LABEL
buckets to "Låg") - see module docstring intent: no scope change means no
confidence change.

Written to Signal.source_notes (public, sourced research - see
app/models/signal.py's docstring), not Signal.notes (private). No new Source
row: the user's citations are domain-only (no specific document URLs), the
same inline-citation convention used elsewhere for domain-only references
(see scripts/update_fty_emas_details.py's GA Statewide Aviation System Plan
note).

Safe to re-run: guarded by checking whether the note's fixed lead-in phrase
already appears in the signal's source_notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Signal
from scripts.annotate_signal import append_note

NOTE = (
    "Uppdatering aug 2026: banan ska flyttas 80 fot västerut och breddas "
    "från 100 till 150 fot (för FAA:s aktuella Runway Design Code-"
    "standarder), del av ett ~575 MUSD moderniseringsprogram, byggstart "
    "april 2027. INGEN av de senaste offentliga källorna (aspenairport.com, "
    "Pitkin County, flera nyhetsartiklar aug 2026) nämner EMAS specifikt i "
    "projektets scope - osäkerheten kring om EMAS ingår kvarstår, snarare "
    "bekräftad än löst. Källor: aspenairport.com/construction, "
    "pitkincounty.com (2026-05-28), simpleflying.com (2026-06-01)."
)


def update_ase_signal(session: Session, *, today: date | None = None) -> tuple[Signal, bool]:
    today = today or date.today()

    ase = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "ASE", Airport.icao_code == "KASE", Airport.faa_code == "ASE"))
    )
    if ase is None:
        raise SystemExit("No airport with code=ASE.")

    signals = session.scalars(select(Signal).where(Signal.airport_id == ase.id)).all()
    if len(signals) != 1:
        raise SystemExit(f"Expected exactly one Signal for ASE, found {len(signals)}.")
    signal = signals[0]

    if "Uppdatering aug 2026" in (signal.source_notes or ""):
        return signal, False

    signal.source_notes = append_note(signal.source_notes, NOTE, on=today)
    session.commit()
    session.refresh(signal)
    return signal, True


def main() -> None:
    with SessionLocal() as session:
        signal, updated = update_ase_signal(session)
    print(f"Signal {signal.id} ({signal.title!r}) updated={updated}. confidence={signal.confidence}")


if __name__ == "__main__":
    main()
