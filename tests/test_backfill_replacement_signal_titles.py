from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal
from scripts.backfill_replacement_signal_titles import backfill


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_backfill_rewrites_old_title_with_airport_name(session):
    airport = Airport(name="Bob Hope", faa_code="BUR", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        airport=airport,
        title="Replacement expected after incident on 2017-04-01",
        category="replacement_after_incident",
        confidence="high",
    )
    session.add(signal)
    session.commit()

    stats = backfill(session)
    session.commit()

    assert stats["updated"] == 1
    assert signal.title == "Bob Hope — EMAS-ersättning väntas efter incident (2017-04-01)"


def test_backfill_includes_runway_designation_when_known(session):
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(airport=airport, designation="6/24")
    session.add(runway)
    session.flush()
    signal = Signal(
        airport=airport,
        runway=runway,
        title="Replacement expected after incident on 2020-01-01",
        category="replacement_after_incident",
        confidence="high",
    )
    session.add(signal)
    session.commit()

    stats = backfill(session)

    assert stats["updated"] == 1
    assert signal.title == "Test Airport — Runway 6/24 EMAS-ersättning väntas efter incident (2020-01-01)"


def test_backfill_leaves_unrelated_titles_untouched(session):
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        airport=airport,
        title="Manually entered signal",
        category="new_installation",
        confidence="planned",
    )
    session.add(signal)
    session.commit()

    stats = backfill(session)

    assert stats["updated"] == 0
    assert signal.title == "Manually entered signal"


def test_backfill_is_idempotent_on_rerun(session):
    airport = Airport(name="Bob Hope", faa_code="BUR", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        airport=airport,
        title="Replacement expected after incident on 2017-04-01",
        category="replacement_after_incident",
        confidence="high",
    )
    session.add(signal)
    session.commit()

    first = backfill(session)
    session.commit()
    second = backfill(session)

    assert first["updated"] == 1
    assert second["updated"] == 0
