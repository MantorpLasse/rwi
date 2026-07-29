from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal, Source
from scripts.add_mdw_emas_bed_repairs_signal import (
    CIP_URL,
    SIGNAL_TITLE,
    add_mdw_emas_bed_repairs_signal,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_mdw(session: Session) -> int:
    airport = Airport(iata_code="MDW", icao_code="KMDW", faa_code="MDW", name="Chicago Midway International Airport", country="USA")
    session.add(airport)
    session.commit()
    return airport.id


def test_creates_signal_with_expected_fields():
    Session = session_factory()
    with Session() as session:
        _seed_mdw(session)

        signal, created = add_mdw_emas_bed_repairs_signal(session)

        assert created is True
        assert signal.title == SIGNAL_TITLE
        assert signal.category == "maintenance"
        assert signal.confidence == "high"
        assert signal.status == "design"
        assert signal.planning_year == 2025
        assert signal.estimated_total_value_usd == 880_000
        assert signal.estimated_emas_value_usd == 880_000
        assert signal.probability_score == 8.0
        assert signal.runway_id is None  # no runway named in the source
        assert "PDF-sida 34" in signal.source_notes
        assert signal.notes is None  # public sourced research, not a private annotation


def test_creates_source_with_page_number_and_no_fabricated_details():
    Session = session_factory()
    with Session() as session:
        _seed_mdw(session)

        signal, _ = add_mdw_emas_bed_repairs_signal(session)

        assert signal.source is not None
        assert signal.source.url == CIP_URL
        assert signal.source.source_type == "CIP"
        assert signal.source.page_number == "34"


def test_is_idempotent_and_does_not_duplicate_signal_or_source():
    Session = session_factory()
    with Session() as session:
        _seed_mdw(session)

        add_mdw_emas_bed_repairs_signal(session)
        signal, created_second_run = add_mdw_emas_bed_repairs_signal(session)

        assert created_second_run is False
        signals = session.scalars(select(Signal).where(Signal.title == SIGNAL_TITLE)).all()
        assert len(signals) == 1
        sources = session.scalars(select(Source).where(Source.url == CIP_URL)).all()
        assert len(sources) == 1


def test_raises_if_mdw_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            add_mdw_emas_bed_repairs_signal(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
