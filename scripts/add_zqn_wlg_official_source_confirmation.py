"""Attach official-airport-page primary sources to Queenstown and
Wellington, replacing the weaker shareholder-newsletter sources both
currently cite (Installation/Signal only hold one source_id each - the old
rows are left in place, just unlinked, per the pattern in
scripts/update_cgh_emas_details.py).

- Queenstown (ZQN) Installation: confirms install_year=2025 (completed
  2025-03-12) via the airport's own project-completion media release.
  Adds budget/RESA/block-count/build-time detail, and notes that the
  release names Downer alongside Runway Safe - further primary-source
  confirmation of the already-recorded confirmed_vendor="Runway Safe"
  (not changed to include Downer; that field records the EMAS vendor
  specifically, not every project partner).
- Wellington (WLG) Signal: confirms the +143m landing / +37m takeoff
  buffer-zone figures via the airport's own official page (2026-03-25),
  a stronger citation than the shareholder letter it previously cited.

Safe to re-run: each Source is looked up by url before creating a
duplicate, and each note-append is guarded by checking whether that
url already appears in the target row's notes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation, Signal, Source
from scripts.annotate_signal import append_note

ZQN_SOURCE_URL = (
    "https://www.queenstownairport.co.nz/corporate/news-media/media-releases/"
    "queenstown-airport-celebrates-end-of-emas-project/"
)
WLG_SOURCE_URL = "https://www.wellingtonairport.co.nz/news/airport-projects/emas-new-runway-buffer-zones/"


def get_or_create_source(session: Session, *, url: str, **fields) -> Source:
    source = session.scalar(select(Source).where(Source.url == url))
    if source is not None:
        return source
    source = Source(url=url, **fields)
    session.add(source)
    session.flush()
    return source


def update_zqn(session: Session, *, today: date) -> tuple[Installation, bool]:
    installation = session.scalar(
        select(Installation).where(Installation.airport.has(iata_code="ZQN"))
    )
    if installation is None:
        raise SystemExit("No ZQN Installation - run scripts/add_rw_shareholder_letter_signals.py first.")

    source = get_or_create_source(
        session,
        url=ZQN_SOURCE_URL,
        title="Queenstown Airport celebrates end of EMAS project",
        source_type="news",
        publisher="Queenstown Airport",
        retrieved_at=today,
        summary=(
            "Officiellt pressmeddelande om slutfört EMAS-projekt: budget 23 MNZD, "
            "RESA okad fran 90m till effektivt 240m, 4870 EMAS-block, 22 veckors byggtid. "
            "Partners Downer och Runway Safe namngivna direkt av flygplatsen."
        ),
    )
    installation.source = source
    installation.install_year = 2025  # confirmed, unchanged

    if ZQN_SOURCE_URL in (installation.notes or ""):
        return installation, False

    note = (
        "Bekraftat via Queenstown Airports officiella pressmeddelande "
        f"({ZQN_SOURCE_URL}): klart 2025-03-12. Budget 23 MNZD. RESA okad fran 90m "
        "till effektivt 240m. 4870 EMAS-block. 22 veckors byggtid. Partners Downer och "
        "Runway Safe namngivna direkt av flygplatsen - ytterligare primarkallebekraftelse "
        "av confirmed_vendor (ej andrat till att inkludera Downer; faltet avser "
        "EMAS-leverantoren specifikt, inte alla projektpartners)."
    )
    installation.notes = append_note(installation.notes, note, on=today)
    return installation, True


def update_wlg(session: Session, *, today: date) -> tuple[Signal, bool]:
    signal = session.scalar(
        select(Signal).where(Signal.airport.has(iata_code="WLG"), Signal.confirmed_vendor.is_not(None))
    )
    if signal is None:
        raise SystemExit("No WLG Signal - run scripts/add_rw_shareholder_letter_signals.py first.")

    source = get_or_create_source(
        session,
        url=WLG_SOURCE_URL,
        title="EMAS - new runway buffer zones",
        source_type="news",
        publisher="Wellington Airport",
        published_date=date(2026, 3, 25),
        retrieved_at=today,
        summary=(
            "Officiell sida om EMAS-buffertzonerna: landningsstracka +143m, "
            "startstracka +37m."
        ),
    )
    signal.source = source

    if WLG_SOURCE_URL in (signal.notes or ""):
        return signal, False

    note = (
        "Bekraftat via Wellington Airports officiella sida "
        f"({WLG_SOURCE_URL}, publicerad 2026-03-25): EMAS ger nya buffertzoner - "
        "landningsstracka +143m, startstracka +37m. Starkare, officiell primarkalla "
        "an tidigare aktieagarbrev-kalla - ersatter den som signalens kalla."
    )
    signal.notes = append_note(signal.notes, note, on=today)
    return signal, True


def seed(session: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    _, zqn_updated = update_zqn(session, today=today)
    _, wlg_updated = update_wlg(session, today=today)
    session.commit()
    return {"zqn_updated": zqn_updated, "wlg_updated": wlg_updated}


def main() -> None:
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
