from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from scripts.apply_svaga_poster_followup import BGM_AMPU_URL, seed


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_bgm_and_vpc(session: Session) -> dict:
    bgm = Airport(iata_code="BGM", icao_code="KBGM", name="Greater Binghamton Airport", country="USA")
    vpc = Airport(iata_code="VPC", icao_code="KVPC", name="Cartersville Airport", country="USA")
    session.add_all([bgm, vpc])
    session.flush()

    broome_source = Source(
        title="2026-2031 Capital Improvements Program", source_type="news",
        url="https://broomecountyny.gov/",
    )
    usaspending_sources = {
        59: Source(title="USAspending grant: Broome County FY2021", source_type="usaspending_grant",
                    url="https://www.usaspending.gov/award/ASST_NON_33600080812021_069"),
        49: Source(title="USAspending grant: Broome County FY2023 (5.4M)", source_type="usaspending_grant",
                    url="https://www.usaspending.gov/award/ASST_NON_33600080882023_069"),
        55: Source(title="USAspending grant: Broome County FY2023 (1.6M)", source_type="usaspending_grant",
                    url="https://www.usaspending.gov/award/ASST_NON_33600080892023_069"),
        58: Source(title="USAspending grant: Broome County FY2023 (1.0M)", source_type="usaspending_grant",
                    url="https://www.usaspending.gov/award/ASST_NON_33600080902023_069"),
        60: Source(title="USAspending grant: Broome County FY2026", source_type="usaspending_grant",
                    url="https://www.usaspending.gov/award/ASST_NON_33600080982026_069"),
    }
    session.add(broome_source)
    session.add_all(usaspending_sources.values())
    session.flush()

    signal_6 = Signal(
        id=6, airport=bgm, source=broome_source, title="Runway 16 departure EMAS project",
        category="new_installation", confidence="programmed",
    )
    grant_signals = {
        59: Signal(id=59, airport=bgm, source=usaspending_sources[59], title="USAspending grant - $481K, FY2021", category="replacement", confidence="high"),
        49: Signal(id=49, airport=bgm, source=usaspending_sources[49], title="USAspending grant - $5.4M, FY2023", category="replacement", confidence="high"),
        55: Signal(id=55, airport=bgm, source=usaspending_sources[55], title="USAspending grant - $1.6M, FY2023", category="replacement", confidence="high"),
        58: Signal(id=58, airport=bgm, source=usaspending_sources[58], title="USAspending grant - $1.0M, FY2023", category="replacement", confidence="high"),
        60: Signal(id=60, airport=bgm, source=usaspending_sources[60], title="USAspending grant - $415K, FY2026", category="replacement", confidence="high"),
    }
    session.add(signal_6)
    session.add_all(grant_signals.values())

    vpc_installation = Installation(
        id=23, airport=vpc, type="EMASMAX", status="active", notes="FAA map region: Map - Main",
    )
    session.add(vpc_installation)
    session.commit()

    return {
        "broome_source_id": broome_source.id,
        "usaspending_source_ids": {k: v.id for k, v in usaspending_sources.items()},
    }


def test_seed_repoints_signal_6_and_sets_target_year():
    Session = session_factory()
    with Session() as session:
        ids = _seed_bgm_and_vpc(session)

        stats = seed(session, today=date(2026, 7, 27))

        assert stats["signal_6_updated"] is True
        signal_6 = session.get(Signal, 6)
        assert signal_6.source.url == BGM_AMPU_URL
        assert signal_6.source_id != ids["broome_source_id"]
        assert signal_6.target_year == 2028
        assert "broomecountyny.gov" in signal_6.notes  # old source cited by url in the note
        # old source row left in place, just unlinked
        assert session.get(Source, ids["broome_source_id"]) is not None


def test_seed_leaves_usaspending_grant_signal_sources_untouched_but_adds_notes():
    Session = session_factory()
    with Session() as session:
        ids = _seed_bgm_and_vpc(session)

        stats = seed(session, today=date(2026, 7, 27))

        assert stats["grant_signals_updated"] == 5
        for signal_id, source_id in ids["usaspending_source_ids"].items():
            signal = session.get(Signal, signal_id)
            assert signal.source_id == source_id  # unchanged - still the specific USAspending award
            assert BGM_AMPU_URL in signal.notes  # cross-referenced by url in notes


def test_seed_downgrades_vpc_installation_notes():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_and_vpc(session)

        stats = seed(session, today=date(2026, 7, 27))

        assert stats["vpc_updated"] is True
        installation = session.get(Installation, 23)
        assert "FAA map region: Map - Main" in installation.notes  # old note preserved
        assert "Låg" in installation.notes
        assert installation.install_year is None  # untouched


def test_seed_only_creates_one_new_source():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_and_vpc(session)

        seed(session, today=date(2026, 7, 27))

        ampu_sources = session.scalars(select(Source).where(Source.url == BGM_AMPU_URL)).all()
        assert len(ampu_sources) == 1


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_and_vpc(session)

        seed(session, today=date(2026, 7, 27))
        stats_second_run = seed(session, today=date(2026, 7, 28))

        assert stats_second_run == {
            "signal_6_updated": False,
            "grant_signals_updated": 0,
            "vpc_updated": False,
        }
        assert len(session.scalars(select(Source).where(Source.url == BGM_AMPU_URL)).all()) == 1
