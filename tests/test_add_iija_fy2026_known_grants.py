from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal, Source
from scripts.add_iija_fy2026_known_grants import CLT_APRON_GUARD_NOTE, seed


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_airports_and_signals(session: Session) -> dict:
    mht = Airport(iata_code="MHT", faa_code="MHT", name="Manchester-Boston Regional Airport", country="USA")
    bos = Airport(iata_code="BOS", faa_code="BOS", name="Boston Logan International Airport", country="USA")
    morristown = Airport(name="Town Of Morristown", city="Morristown", state_region="New Jersey", country="USA")
    clt = Airport(faa_code="CLT", name="Charlotte Douglas International", country="USA")
    session.add_all([mht, bos, morristown, clt])
    session.flush()

    mht_original_source = Source(title="Runway 6 Departure End EMAS Project", source_type="Procurement", reliability_level="official")
    mht_usaspending_source = Source(title="USAspending grant: Manchester, City Of EMAS", source_type="usaspending_grant", reliability_level="official")
    bos_faa_source = Source(title="FAA Airport Construction Impact Report", source_type="faa_construction_report", reliability_level="official")
    bos_usaspending_source = Source(title="USAspending grant: Massachusetts Port Authority EMAS", source_type="usaspending_grant", reliability_level="official")
    mmu_usaspending_source = Source(title="USAspending grant: Town Of Morristown EMAS", source_type="usaspending_grant", reliability_level="official")
    clt_source = Source(title="Runway Safe aktieägarbrev, maj 2025", source_type="shareholder_newsletter", reliability_level="official")
    session.add_all(
        [mht_original_source, mht_usaspending_source, bos_faa_source, bos_usaspending_source, mmu_usaspending_source, clt_source]
    )
    session.flush()

    mht_signal_unrelated = Signal(
        airport=mht, source=mht_original_source, title="Runway 6 departure-end EMAS replacement",
        category="replacement", confidence="confirmed", notes="Replacement of existing EMAS.",
    )
    mht_signal_target = Signal(
        airport=mht, source=mht_usaspending_source, title="USAspending grant: Manchester, City Of EMAS",
        category="replacement", confidence="high",
        notes="PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA. Rest of the text...",
    )
    bos_signal_target = Signal(
        airport=bos, source=bos_faa_source, title="Runway 9/27 RSA and EMAS phase 2",
        category="new_installation", confidence="confirmed", notes="Continuation of RSA/EMAS construction.",
    )
    bos_signal_unrelated = Signal(
        airport=bos, source=bos_usaspending_source, title="USAspending grant: Massachusetts Port Authority EMAS",
        category="new_installation", confidence="high",
        notes="PURPOSE: CONSTRUCT/EXTEND SAFETY AREA. Funds phase 2 pier construction.",
    )
    mmu_signal_target = Signal(
        airport=morristown, source=mmu_usaspending_source, title="USAspending grant: Town Of Morristown EMAS",
        category="new_installation", confidence="high",
        notes="PURPOSE: CONSTRUCT/EXTEND SAFETY AREA. Phase 11, EMAS blocks for Runway 23.",
    )
    clt_signal = Signal(
        airport=clt, source=clt_source, title="Charlotte Douglas EMAS-order (Runway Safe bekräftad leverantör)",
        category="new_installation", confidence="high", confirmed_vendor="Runway Safe", notes="Ny order signerad.",
    )
    session.add_all(
        [mht_signal_unrelated, mht_signal_target, bos_signal_target, bos_signal_unrelated, mmu_signal_target, clt_signal]
    )
    session.commit()

    return {
        "mht_signal_target_id": mht_signal_target.id,
        "mht_signal_unrelated_id": mht_signal_unrelated.id,
        "bos_signal_target_id": bos_signal_target.id,
        "bos_signal_unrelated_id": bos_signal_unrelated.id,
        "mmu_signal_target_id": mmu_signal_target.id,
        "clt_signal_id": clt_signal.id,
        "morristown_id": morristown.id,
    }


def test_seed_attaches_notes_without_creating_new_signals_or_changing_source_id():
    Session = session_factory()
    with Session() as session:
        ids = _seed_airports_and_signals(session)

        signal_count_before = len(session.scalars(select(Signal)).all())
        stats = seed(session, today=date(2026, 7, 25))

        assert stats["signals_updated"] == 3
        assert stats["clt_guard_note_added"] is True
        assert len(session.scalars(select(Signal)).all()) == signal_count_before  # no new Signal rows

        mht_signal = session.get(Signal, ids["mht_signal_target_id"])
        assert "iija:2026:4:MHT" in mht_signal.notes
        assert "5,100,000" in mht_signal.notes
        assert "[2026-07-25]" in mht_signal.notes
        assert "PURPOSE: RECONSTRUCT" in mht_signal.notes  # original note preserved
        assert mht_signal.source_id is not None
        assert mht_signal.source.source_type == "usaspending_grant"  # source_id untouched

        # The other MHT signal (flymanchester procurement listing) must be untouched.
        mht_unrelated = session.get(Signal, ids["mht_signal_unrelated_id"])
        assert "iija:" not in mht_unrelated.notes

        bos_signal = session.get(Signal, ids["bos_signal_target_id"])
        assert "iija:2026:4:BOS" in bos_signal.notes
        assert "17,500,000" in bos_signal.notes
        assert bos_signal.source.source_type == "faa_construction_report"

        # The BOS USAspending signal (also mentions "phase 2" text) must not get the note -
        # the title match picked the flagship confirmed signal, not this one.
        bos_unrelated = session.get(Signal, ids["bos_signal_unrelated_id"])
        assert "iija:" not in bos_unrelated.notes

        mmu_signal = session.get(Signal, ids["mmu_signal_target_id"])
        assert "iija:2026:6:MMU" in mmu_signal.notes
        assert "1,362,000" in mmu_signal.notes

        morristown = session.get(Airport, ids["morristown_id"])
        assert morristown.faa_code == "MMU"

        clt_signal = session.get(Signal, ids["clt_signal_id"])
        assert CLT_APRON_GUARD_NOTE in clt_signal.notes
        assert "Ny order signerad." in clt_signal.notes  # original note preserved


def test_seed_creates_three_standalone_iija_sources():
    Session = session_factory()
    with Session() as session:
        _seed_airports_and_signals(session)
        seed(session, today=date(2026, 7, 25))

        iija_sources = session.scalars(select(Source).where(Source.source_type == "iija_grant")).all()
        assert len(iija_sources) == 3
        assert {s.external_id for s in iija_sources} == {
            "iija:2026:4:MHT", "iija:2026:4:BOS", "iija:2026:6:MMU",
        }
        assert all(s.url.startswith("https://www.faa.gov/iija/") for s in iija_sources)


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        ids = _seed_airports_and_signals(session)

        seed(session, today=date(2026, 7, 25))
        mht_notes_after_first_run = session.get(Signal, ids["mht_signal_target_id"]).notes

        stats_second_run = seed(session, today=date(2026, 7, 26))

        assert stats_second_run == {
            "signals_updated": 0,
            "signals_already_up_to_date": 3,
            "clt_guard_note_added": False,
        }
        mht_signal = session.get(Signal, ids["mht_signal_target_id"])
        assert mht_signal.notes == mht_notes_after_first_run  # not appended twice
        assert len(session.scalars(select(Source).where(Source.source_type == "iija_grant")).all()) == 3
