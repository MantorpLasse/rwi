"""Confirm PHL's Runway 8-26 EMAS completion with a second corroborating
source, and link the still-open USAspending grant Signal (id 43) to the
Installation that already documents the built system.

Investigation before writing this script found the request's premise only
half true: Signal 43 ("USAspending grant - $8.5M, FY2024") was indeed still
sitting as status="identified" with no installation_id. But Installation 147
(source_id=61, phl.org's own "PHL Completes EMAS on Runway 8-26" page) was
*already* created independently - during the 2026-07-26 FAA-fact-sheet/
install-year backfill session (commit a392cf6), not via
scripts/graduate_signal_to_installation.py - and its own notes explicitly
say it "matchar den redan befintliga USAspending-signalen pa $8,5M FY2024",
i.e. it already knew about Signal 43 without ever being linked to it.

Running graduate_signal_to_installation.py on Signal 43 as originally asked
would therefore have created a *second*, duplicate Installation for the same
real-world system. This script links Signal 43 to the existing Installation
147 instead (same net effect the graduation script produces - status flips
to "completed", installation_id gets set - just without the duplicate row).

Also resolves a date discrepancy the user's brief didn't have quite right:
they cited 2026-06-12 as the inauguration date. Independent research (via
the same two sources they named - phl.org/newsroom/EMAS and CBS News
Philadelphia - plus 6abc, 92.5XTU, WMGK, WJBR, delco.today) places the
public ribbon-cutting/press event on 2025-08-26, while phl.org's own body
text (already captured in Installation 147's notes before this script ran)
gives 2025-06-12 as the actual construction-completion date. User confirmed
via AskUserQuestion to go with the 2025 dates over the originally-suggested
2026-06-12.

Adds one new fact not yet in the DB: construction started September 2024
(from phl.org). Adds one new corroborating source (CBS News Philadelphia)
carrying Mayor Cherelle Parker's on-the-record "There is a total of 117
systems across the United States" quote, cited by the user as the current
national comparison point.

Safe to re-run: the CBS Source is looked up by url before creating a
duplicate, and each note addition is guarded by checking whether that url
already appears in the target row's text.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation, Signal, Source
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

SIGNAL_ID = 43
INSTALLATION_ID = 147

PHL_SOURCE_URL = "https://www.phl.org/newsroom/EMAS"
CBS_SOURCE_URL = "https://www.cbsnews.com/philadelphia/news/emas-philadelphia-international-airport"

CONSTRUCTION_COMPLETION = date(2025, 6, 12)  # phl.org body text, already reflected in Installation 147's notes
PRESS_EVENT_DATE = date(2025, 8, 26)  # ribbon-cutting covered by CBS News Philadelphia, 6abc, et al.
CONSTRUCTION_START = date(2024, 9, 1)  # phl.org: "began in September 2024" - exact day not given


def confirm(session: Session, *, today: date | None = None) -> tuple[Signal, Installation, bool]:
    today = today or date.today()

    signal = session.get(Signal, SIGNAL_ID)
    if signal is None:
        raise SystemExit(f"No signal with id={SIGNAL_ID}")
    installation = session.get(Installation, INSTALLATION_ID)
    if installation is None:
        raise SystemExit(f"No installation with id={INSTALLATION_ID}")
    if signal.airport_id != installation.airport_id:
        raise SystemExit("Signal and Installation airport_id mismatch - refusing to link.")

    cbs_source = get_or_create_source(
        session,
        url=CBS_SOURCE_URL,
        title="EMAS runway safety system unveiled at Philadelphia International Airport",
        source_type="news",
        publisher="CBS News Philadelphia",
        published_date=PRESS_EVENT_DATE,
        retrieved_at=today,
        summary=(
            "CBS News Philadelphia coverage of the PHL EMAS ribbon-cutting. Corroborates "
            "phl.org's own figures ($8.5M, FAA Airport Infrastructure Grant, Runway 8-26) "
            "and adds Mayor Cherelle Parker's on-the-record quote: 'There is a total of "
            "117 systems across the United States.'"
        ),
    )

    already_linked = signal.status == "completed" and signal.installation_id == INSTALLATION_ID

    if not already_linked:
        if signal.status == "completed":
            raise SystemExit(
                f"Signal {SIGNAL_ID} is already completed but linked to a different "
                f"installation_id={signal.installation_id} - refusing to overwrite."
            )
        signal.status = "completed"
        signal.installation = installation
        signal.completion_date = CONSTRUCTION_COMPLETION
        signal.confirmed_vendor = "Runway Safe"

    signal_note = (
        "Bekraftat fardigbyggt och kopplat till Installation "
        f"{INSTALLATION_ID}: byggstart september 2024, konstruktion fardig "
        f"{CONSTRUCTION_COMPLETION.isoformat()}, offentlig invigning/press "
        f"{PRESS_EVENT_DATE.isoformat()}. Borgmastare Cherelle Parker citerad av CBS News "
        "Philadelphia: \"There is a total of 117 systems across the United States.\" "
        f"Kallor: {PHL_SOURCE_URL} , {CBS_SOURCE_URL}"
    )
    signal_updated = False
    if CBS_SOURCE_URL not in (signal.source_notes or ""):
        signal.source_notes = append_note(signal.source_notes, signal_note, on=today)
        signal_updated = True

    installation_note = (
        f"Byggstart september {CONSTRUCTION_START.year} (fore konstruktionen "
        f"fardigstalldes {CONSTRUCTION_COMPLETION.isoformat()}). Offentlig invignings-/"
        f"pressevent {PRESS_EVENT_DATE.isoformat()}: borgmastare Cherelle Parker citerad av "
        "CBS News Philadelphia: \"There is a total of 117 systems across the United "
        f"States.\" Kalla: {CBS_SOURCE_URL}"
    )
    installation_updated = False
    if CBS_SOURCE_URL not in (installation.notes or ""):
        installation.notes = append_note(installation.notes, installation_note, on=today)
        installation_updated = True

    session.commit()
    session.refresh(signal)
    session.refresh(installation)
    return signal, installation, (signal_updated or installation_updated or not already_linked)


def main() -> None:
    with SessionLocal() as session:
        signal, installation, updated = confirm(session)
    print(
        f"Signal {signal.id} status={signal.status} installation_id={signal.installation_id} "
        f"updated={updated}"
    )
    print(f"Installation {installation.id} confirmed_vendor={installation.confirmed_vendor}")


if __name__ == "__main__":
    main()
