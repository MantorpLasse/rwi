from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal, Source
from scripts.rename_usaspending_signal_titles import rename_titles


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_bos_style_signals(session: Session) -> dict:
    airport = Airport(iata_code="BOS", name="Boston Logan International Airport", country="USA")
    session.add(airport)
    session.flush()

    sources = [Source(title="USAspending grant: Massachusetts Port Authority", source_type="usaspending_grant", reliability_level="official") for _ in range(3)]
    session.add_all(sources)
    session.flush()

    signals = [
        Signal(
            airport=airport, source=sources[0], title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation", confidence="high",
            estimated_total_value_usd=Decimal("56187750"), planning_year=2025,
        ),
        Signal(
            airport=airport, source=sources[1], title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation", confidence="high",
            estimated_total_value_usd=Decimal("8983669"), planning_year=2026,
        ),
        Signal(
            airport=airport, source=sources[2], title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation", confidence="high",
            estimated_total_value_usd=Decimal("60311.22"), planning_year=2023,
        ),
    ]
    session.add_all(signals)

    unrelated_source = Source(title="FAA Airport Construction Impact Report", source_type="faa_construction_report", reliability_level="official")
    session.add(unrelated_source)
    session.flush()
    unrelated_signal = Signal(
        airport=airport, source=unrelated_source, title="Runway 9/27 RSA and EMAS phase 2",
        category="new_installation", confidence="confirmed",
    )
    session.add(unrelated_signal)
    session.commit()
    return {
        "signal_ids": [s.id for s in signals],
        "unrelated_signal_id": unrelated_signal.id,
    }


def test_rename_titles_makes_each_grant_unique():
    Session = session_factory()
    with Session() as session:
        ids = _seed_bos_style_signals(session)

        stats = rename_titles(session)

        assert stats == {"renamed": 3, "already_renamed": 0}

        titles = {session.get(Signal, sid).title for sid in ids["signal_ids"]}
        assert titles == {
            "USAspending grant — $56.2M, FY2025",
            "USAspending grant — $9.0M, FY2026",
            "USAspending grant — $60K, FY2023",
        }
        assert len(titles) == 3  # no longer identical


def test_rename_titles_leaves_non_usaspending_signals_untouched():
    Session = session_factory()
    with Session() as session:
        ids = _seed_bos_style_signals(session)

        rename_titles(session)

        unrelated = session.get(Signal, ids["unrelated_signal_id"])
        assert unrelated.title == "Runway 9/27 RSA and EMAS phase 2"


def test_rename_titles_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_bos_style_signals(session)

        rename_titles(session)
        stats_second_run = rename_titles(session)

        assert stats_second_run == {"renamed": 0, "already_renamed": 3}
