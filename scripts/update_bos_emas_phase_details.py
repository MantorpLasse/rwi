"""One-off enrichment of Boston Logan's (BOS) Runway 27 EMAS phase-2 Signal
(id 3) confirming two facts from new research (Massport / Revere Journal,
2026-08-12):

1. Phase 1 (the RSA/EMAS work the signal's existing source_notes already
   describes as "continued from 2025 Phase 1") is now confirmed complete as
   of fall 2025.
2. BOS already operates two other EMAS installations - Runway 22R and
   Runway 33L - in addition to this ongoing Runway 27 project. Both are
   already reflected in this DB as Installation 33 and Installation 107
   (their notes cite the FAA EMAS fact sheets' "04L/22R/04L, 15R/33L/15R"
   and "2 system: A (2005) och B (2006...)" entries) - this script doesn't
   touch those rows, it just cross-references them from the Signal's public
   notes per the user's request.

Appended to Signal.source_notes (public, sourced research - see
app/models/signal.py's docstring), not Signal.notes (private). Follows the
inline-citation style already used elsewhere in this same signal's
source_notes (the 2026-07-25 IIJA-grant note cites a URL directly without a
separate Source row) rather than creating a formal Source row for the
Revere Journal article.

Safe to re-run: the note addition is guarded by checking whether the Revere
Journal URL already appears in the signal's source_notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Signal
from scripts.annotate_signal import append_note

SIGNAL_ID = 3
REVERE_JOURNAL_URL = (
    "https://reverejournal.com/2026/08/12/massport-to-begin-phase-2-of-runway-safety-work-at-boston-logan/"
)

NOTE = (
    "Massport bekraftar (Revere Journal, 2026-08-12) att fas 1 av detta "
    "Runway 27 RSA/EMAS-projekt fardigstalldes hosten 2025 - fas 2 (denna "
    "signal) ar en direkt fortsattning. Utover detta pagaende arbete har "
    "Boston Logan redan tva andra EMAS-installationer i drift: Runway 22R "
    "och Runway 33L (registrerade i denna databas som Installation 33 och "
    f"Installation 107). Kalla: {REVERE_JOURNAL_URL}"
)


def update_bos_signal(session: Session, *, today: date | None = None) -> tuple[Signal, bool]:
    today = today or date.today()

    signal = session.get(Signal, SIGNAL_ID)
    if signal is None:
        raise SystemExit(f"No signal with id={SIGNAL_ID}")

    if REVERE_JOURNAL_URL in (signal.source_notes or ""):
        return signal, False

    signal.source_notes = append_note(signal.source_notes, NOTE, on=today)
    session.commit()
    session.refresh(signal)
    return signal, True


def main() -> None:
    with SessionLocal() as session:
        signal, updated = update_bos_signal(session)
    print(f"Signal {signal.id} ({signal.title!r}) updated={updated}.")


if __name__ == "__main__":
    main()
