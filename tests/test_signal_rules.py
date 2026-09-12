from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Incident, Runway, Signal, Source
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.services import add_source_and_flag_keywords


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def make_airport(**overrides) -> Airport:
    defaults = dict(name="Test Airport", iata_code="TST", country="USA")
    defaults.update(overrides)
    return Airport(**defaults)


# Rule 1: an Incident insert always creates a matching high-confidence Signal.


def test_incident_insert_creates_replacement_signal(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    incident = Incident(
        airport=airport,
        incident_date=date(2026, 3, 1),
        incident_type="overrun",
        summary="Aircraft overran the runway and engaged the EMAS bed.",
    )
    session.add(incident)
    session.commit()

    signals = session.scalars(select(Signal).where(Signal.airport_id == airport.id)).all()
    assert len(signals) == 1
    signal = signals[0]
    assert signal.category == "replacement_after_incident"
    assert signal.confidence == "high"
    assert signal.notes == incident.summary
    assert signal.airport_id == airport.id
    assert signal.runway_id is None
    # A real status (not None) so /signals doesn't render an empty status
    # badge next to "high" confidence, which reads as if status == "high".
    assert signal.status == "identified"
    # So the signal can surface in score-sorted views instead of always
    # sorting last behind every manually-scored signal.
    assert signal.probability_score == DEFAULT_SCORE_BY_CONFIDENCE["high"]
    # RWI HQ "Trust Preconditions for Update & Report V1" mission, Part 1:
    # this Signal has no SourceAssertion, no ReviewerAction, no human
    # review step - it must never reach the public site automatically.
    assert signal.published is False


# RWI HQ "Trust Preconditions for Update & Report V1" mission, Part 1,
# TESTS - INCIDENT item 4: source_id/runway_id linkage is preserved
# unchanged (this test uses non-None values, unlike the test above, so a
# regression that silently dropped the linkage would be caught here).
def test_incident_signal_preserves_source_and_runway_linkage(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    runway = Runway(airport=airport, designation="09/27")
    session.add(runway)
    source = Source(title="NTSB preliminary report", source_type="incident_report", url="https://example.test/ntsb")
    session.add(source)
    session.flush()

    incident = Incident(
        airport=airport,
        runway=runway,
        source=source,
        incident_date=date(2026, 4, 12),
        incident_type="overrun",
        summary="Aircraft overran the runway and engaged the EMAS bed.",
    )
    session.add(incident)
    session.commit()

    signal = session.scalars(select(Signal).where(Signal.airport_id == airport.id)).one()
    assert signal.runway_id == runway.id
    assert signal.source_id == source.id
    assert signal.published is False


# RWI HQ "Trust Preconditions for Update & Report V1" mission, Part 1,
# TESTS - INCIDENT item 6: the mission explicitly forbids changing
# Signal.published's global column-level default - only this one
# creation path now passes it explicitly. A bare Signal with no explicit
# `published` argument must still default to True, exactly as before this
# mission.
def test_signal_published_default_remains_true_globally(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    signal = Signal(airport=airport, title="Manually added signal", category="unknown", confidence="low")
    session.add(signal)
    session.commit()

    assert signal.published is True


def test_incident_can_opt_out_of_the_automatic_signal(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    session.add(
        Incident(
            airport=airport,
            incident_date=date(2026, 3, 1),
            incident_type="overrun",
            implies_replacement=False,
        )
    )
    session.commit()

    assert session.scalars(select(Signal)).all() == []


# Rule 2: adding a Source whose text mentions EMAS/RSA keywords flags a low-confidence Signal.


def test_source_with_keyword_creates_low_confidence_signal(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    source = Source(
        title="2026 Airport Master Plan - Runway Safety Area Extension",
        source_type="master_plan",
        url="https://example.test/master-plan.pdf",
    )

    signal = add_source_and_flag_keywords(session, airport=airport, source=source)
    session.commit()

    assert signal is not None
    assert signal.category == "unknown"
    assert signal.confidence == "low"
    assert "runway safety area" in signal.notes.lower()
    assert signal.source_id == source.id
    assert signal.airport_id == airport.id
    assert signal.status == "identified"
    assert signal.probability_score == DEFAULT_SCORE_BY_CONFIDENCE["low"]

    persisted = session.scalars(select(Signal)).all()
    assert len(persisted) == 1


def test_source_without_keyword_creates_no_signal(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    source = Source(
        title="Annual noise abatement report",
        source_type="news",
        url="https://example.test/noise-report.pdf",
    )

    signal = add_source_and_flag_keywords(session, airport=airport, source=source)
    session.commit()

    assert signal is None
    assert session.scalars(select(Signal)).all() == []
    # The source itself is still saved even without a keyword hit.
    assert session.scalar(select(Source).where(Source.id == source.id)) is not None


def test_keyword_match_checks_summary_and_document_reference_too(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    source = Source(
        title="FY2026 grant announcement",
        source_type="aip_grant",
        url="https://example.test/grant.pdf",
        summary="Includes funding for an arresting system upgrade.",
    )

    signal = add_source_and_flag_keywords(session, airport=airport, source=source)

    assert signal is not None
    assert "arresting system" in signal.notes.lower()


def test_keyword_match_is_case_insensitive(session):
    airport = make_airport()
    session.add(airport)
    session.flush()

    source = Source(
        title="emas replacement study",
        source_type="study",
        url="https://example.test/study.pdf",
    )

    signal = add_source_and_flag_keywords(session, airport=airport, source=source)

    assert signal is not None
    assert "emas" in signal.notes.lower()
