"""One-off data entry from Runway Safe's own shareholder newsletters - the
company's confirmation of its own awarded contracts, which is about as
authoritative as a "confirmed vendor" fact gets. See app/models/signal.py's
confirmed_vendor / app/models/installation.py's confirmed_vendor for why
that's tracked as its own field rather than folded into likely_supplier.

Adds/updates:
- Queenstown (ZQN, new airport): a confirmed 2025 Installation - first
  EMAS in Oceania, per the 2024-07-15 letter.
- Wellington (WLG, new airport): a new_installation Signal for an order
  signed per the 2025-05 letter, kept distinct from WLG's own press
  releases (physical completion March 2026) per the notes.
- Charlotte Douglas (CLT, already existed from FAA data) and Minneapolis-
  St Paul (MSP, already existed) each get a *new* Signal for this order -
  their existing Installation/Signal history from earlier imports is left
  untouched.
- Santos Dumont (SDU, existing Signal from scripts/add_brazil_expansion.py)
  gets confirmed_vendor set and a dated note appended (existing notes and
  its Agência iNFRA source_id are preserved).

Shareholder newsletters have no public URL, so their Source rows use
source_type=shareholder_newsletter with url=None (see
ensure_source_url_nullable) - the letter's date is instead recorded in the
Source title and in each citing row's own notes.

Safe to re-run: new Signals are looked up by (airport_id, title) and the
SDU update only fires once (guarded by confirmed_vendor already being set).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import Airport, Installation, Signal, Source
from scripts.add_brazil_expansion import get_or_create_airport

MAY_2025_LETTER_TITLE = "Runway Safe aktieägarbrev, maj 2025"
QUEENSTOWN_LETTER_TITLE = "Runway Safe aktieägarbrev 2024-07-15"

SHARED_ORDER_CONTEXT = (
    "Ny order signerad enligt RW:s aktieägarbrev (2025-05), del av mUSD 36 "
    "total orderingång jan-apr 2025 (jfr mUSD 19 samma period 2024). "
    "Utestående orderbok mUSD 48 för leverans 2025/2026."
)


def ensure_confirmed_vendor_columns(bind=engine) -> None:
    """Add confirmed_vendor to signals/installations on an older database.

    Base.metadata.create_all() only creates missing tables, not missing
    columns on ones that already exist - idempotent, safe to call every run.
    """
    for table in ("signals", "installations"):
        existing = {c["name"] for c in inspect(bind).get_columns(table)}
        if "confirmed_vendor" not in existing:
            with bind.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN confirmed_vendor VARCHAR(150)"))


def ensure_source_url_nullable(bind=engine) -> None:
    """Relax sources.url from NOT NULL to nullable.

    A shareholder newsletter has no public link, so its Source row needs
    url=None rather than a fabricated URL. SQLite can't ALTER COLUMN
    directly, so this rebuilds the table - existing values (including
    already-populated urls) are copied across unchanged; only the
    constraint is dropped. Idempotent: a no-op once url is nullable.
    """
    columns = inspect(bind).get_columns("sources")
    url_column = next(c for c in columns if c["name"] == "url")
    if url_column["nullable"]:
        return

    with bind.begin() as connection:
        connection.execute(text("ALTER TABLE sources RENAME TO sources_old"))
        connection.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER NOT NULL, "
                "title VARCHAR(300) NOT NULL, "
                "source_type VARCHAR(50) NOT NULL, "
                "publisher VARCHAR(200), "
                "url VARCHAR(1000), "
                "published_date DATE, "
                "retrieved_at DATE, "
                "document_reference VARCHAR(200), "
                "page_number VARCHAR(30), "
                "summary TEXT, "
                "reliability_level VARCHAR(30) NOT NULL, "
                "external_id VARCHAR(200), "
                "PRIMARY KEY (id)"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sources (id, title, source_type, publisher, url, published_date, "
                "retrieved_at, document_reference, page_number, summary, reliability_level, external_id) "
                "SELECT id, title, source_type, publisher, url, published_date, retrieved_at, "
                "document_reference, page_number, summary, reliability_level, external_id FROM sources_old"
            )
        )
        connection.execute(text("DROP TABLE sources_old"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_source_type ON sources (source_type)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_external_id ON sources(external_id)"))


def find_airport_by_code(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            or_(Airport.iata_code == code, Airport.icao_code == code, Airport.faa_code == code)
        )
    )


def get_or_create_source_by_title(session: Session, *, title: str, **fields) -> Source:
    source = session.scalar(select(Source).where(Source.title == title))
    if source is not None:
        return source
    source = Source(title=title, **fields)
    session.add(source)
    session.flush()
    return source


def get_or_create_signal_by_title(session: Session, *, airport_id: int, title: str, **fields) -> tuple[Signal, bool]:
    signal = session.scalar(select(Signal).where(Signal.airport_id == airport_id, Signal.title == title))
    if signal is not None:
        return signal, False
    signal = Signal(airport_id=airport_id, title=title, **fields)
    session.add(signal)
    session.flush()
    return signal, True


def seed(session: Session) -> dict:
    stats = {"installations_created": 0, "signals_created": 0, "sdu_updated": False}

    queenstown_source = get_or_create_source_by_title(
        session,
        title=QUEENSTOWN_LETTER_TITLE,
        source_type="shareholder_newsletter",
        publisher="Runway Safe",
        published_date=date(2024, 7, 15),
        summary=(
            "Kontrakt för två EMAS-installationer vid Queenstown (ZQN), Runway "
            "Safes första i Oceanien; färdigställande väntat H1 2025."
        ),
    )
    may_2025_source = get_or_create_source_by_title(
        session,
        title=MAY_2025_LETTER_TITLE,
        source_type="shareholder_newsletter",
        publisher="Runway Safe",
        summary=(
            "Omsättning 13,4 MUSD (jfr 11), rullande 12 mån 42,4 MUSD, EBITDA "
            "1,9 MUSD (jfr 3,4 - lönsamhet pressad av produktmix/"
            "transportkostnader), eftermarknadsförsäljning 2,4 MUSD (jfr 4,2, "
            "men 2024 hade två exceptionella affärer). Ny orderingång mUSD 36 "
            "jan-apr 2025 (jfr mUSD 19 samma period 2024). Utestående orderbok "
            "mUSD 48 för leverans 2025/2026."
        ),
    )

    zqn = get_or_create_airport(
        session,
        iata_code="ZQN",
        icao_code="NZQN",
        name="Queenstown Airport",
        city="Queenstown",
        state_region="Otago",
        country="New Zealand",
    )
    wlg = get_or_create_airport(
        session,
        iata_code="WLG",
        icao_code="NZWN",
        name="Wellington International Airport",
        city="Wellington",
        state_region="Wellington",
        country="New Zealand",
    )

    existing_zqn_installation = session.scalar(
        select(Installation).where(Installation.airport_id == zqn.id, Installation.install_year == 2025)
    )
    if existing_zqn_installation is None:
        session.add(
            Installation(
                airport=zqn,
                source=queenstown_source,
                type="EMAS",
                install_year=2025,
                status="active",
                confirmed_vendor="Runway Safe",
                notes=(
                    "Kontrakt för två EMAS, första i Oceanien, enligt RW:s "
                    "aktieägarbrev (2024-07-15). Färdigställt H1 2025."
                ),
            )
        )
        stats["installations_created"] += 1

    _, wlg_created = get_or_create_signal_by_title(
        session,
        airport_id=wlg.id,
        title="Wellington EMAS-order (Runway Safe bekräftad leverantör)",
        category="new_installation",
        confidence="high",
        confirmed_vendor="Runway Safe",
        target_year=2026,
        source=may_2025_source,
        notes=(
            SHARED_ORDER_CONTEXT + "\n\n"
            "Order signerad enligt brevet (jan-apr 2025). Håll isär från "
            "tidigare research (WLG:s egna pressmeddelanden) som visar fysisk "
            "installation klar mars 2026 - dvs order 2025, leverans 2026."
        ),
    )
    if wlg_created:
        stats["signals_created"] += 1

    clt = find_airport_by_code(session, "CLT")
    if clt is None:
        raise SystemExit("No airport with iata_code=CLT.")
    _, clt_created = get_or_create_signal_by_title(
        session,
        airport_id=clt.id,
        title="Charlotte Douglas EMAS-order (Runway Safe bekräftad leverantör)",
        category="new_installation",
        confidence="high",
        confirmed_vendor="Runway Safe",
        source=may_2025_source,
        notes=SHARED_ORDER_CONTEXT,
    )
    if clt_created:
        stats["signals_created"] += 1

    msp = find_airport_by_code(session, "MSP")
    if msp is None:
        raise SystemExit("No airport with iata_code=MSP.")
    _, msp_created = get_or_create_signal_by_title(
        session,
        airport_id=msp.id,
        title="MSP EMAS-order (Runway Safe bekräftad leverantör)",
        category="replacement",
        confidence="high",
        confirmed_vendor="Runway Safe",
        source=may_2025_source,
        notes=SHARED_ORDER_CONTEXT,
    )
    if msp_created:
        stats["signals_created"] += 1

    sdu = find_airport_by_code(session, "SDU")
    if sdu is None:
        raise SystemExit("No airport with iata_code=SDU - run scripts/add_brazil_expansion.py first.")
    sdu_signal = session.scalar(
        select(Signal).where(Signal.airport_id == sdu.id, Signal.category == "new_installation")
    )
    if sdu_signal is None:
        raise SystemExit("No new_installation Signal for SDU - run scripts/add_brazil_expansion.py first.")
    if not sdu_signal.confirmed_vendor:
        sdu_signal.confirmed_vendor = "Runway Safe (med Atlantis Consulting)"
        sdu_signal.notes = (
            sdu_signal.notes
            + "\n\nBekräftad leverantör: Runway Safe (med Atlantis Consulting), enligt "
            "LinkedIn-inlägg och RW:s aktieägarbrev (2025-05, ny order signerad "
            "jan-apr 2025).\n"
            + SHARED_ORDER_CONTEXT
            + "\n\nÖppen fråga: är detta Latinamerikas första EMASMax-installation "
            "(jämfört med Congonhas, som var Latinamerikas första EMAS)? Ej "
            "verifierat - håll frågan öppen."
        )
        stats["sdu_updated"] = True

    session.commit()
    return stats


def main() -> None:
    ensure_confirmed_vendor_columns(engine)
    ensure_source_url_nullable(engine)
    with SessionLocal() as session:
        stats = seed(session)
    print(stats)


if __name__ == "__main__":
    main()
