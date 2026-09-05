from datetime import date

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Runway, Signal, Source
from scripts.correct_bgm_signal6_target_year import (
    CORRECTION_NOTE,
    SIGNAL_ID,
    correct_bgm_signal6_target_year,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_bgm_signal6(session, **overrides):
    # id=54/id=6 match the real production Source/Runway ids the script's
    # own preconditions check against - not arbitrary, so the happy-path
    # fixture actually exercises those checks rather than accidentally
    # tripping them via unrelated autoincrement values.
    airport = Airport(iata_code="BGM", icao_code="KBGM", faa_code="BGM", name="Greater Binghamton Airport", country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(id=6, airport_id=airport.id, designation="16/34", length_m=2225)
    source = Source(
        id=54,
        title="Greater Binghamton Airport Master Plan Update - Financial Feasibility (Ch. 8)",
        source_type="master_plan", reliability_level="official",
        url="https://binghamtonairport.com/wp-content/uploads/2023/01/8-BGM-AMPU-Financial-Feasibility.pdf",
    )
    session.add_all([runway, source])
    session.flush()

    defaults = dict(
        id=SIGNAL_ID,
        airport=airport,
        runway=runway,
        source=source,
        title="Runway 16 departure EMAS project",
        category="new_installation",
        confidence="programmed",
        status="funded",
        planning_year=2026,
        target_year=2028,
        published=True,
        source_notes=(
            "Capital program includes EMAS work and final flight-check phase.\n"
            "[2026-07-26] Bekraftat via Greater Binghamton Airports egen 2021 "
            "Airport Master Plan Update: fasindelat projekt, Construction Phase I "
            "+ Phase II (2023-2028)."
        ),
    )
    defaults.update(overrides)
    signal = Signal(**defaults)
    session.add(signal)
    session.commit()
    return signal.id


# 1. Exact expected before-state applies correctly, correction succeeds.
def test_correction_applies_to_matching_before_state():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)

        signal, updated = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))

        assert updated is True
        assert signal.id == SIGNAL_ID


# 2. target_year becomes None.
def test_target_year_cleared():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        signal, _ = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        assert signal.target_year is None


# 3. planning_year remains 2026.
def test_planning_year_unchanged():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        signal, _ = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        assert signal.planning_year == 2026


# 4. Existing source_notes preserved verbatim, not overwritten.
def test_existing_source_notes_preserved():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        signal, _ = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        assert "Capital program includes EMAS work" in signal.source_notes
        assert "[2026-07-26] Bekraftat via" in signal.source_notes
        assert "fasindelat projekt" in signal.source_notes


# 5. Correction note appended, dated, with the expected substance.
def test_correction_note_appended_with_date():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        signal, _ = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        assert "[2026-09-06] " + CORRECTION_NOTE in signal.source_notes
        assert "does not establish 2028 as an EMAS-specific target" in signal.source_notes


# 6. Other Signal 6 fields untouched (only target_year and source_notes change).
def test_other_fields_unaffected():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        signal, _ = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        assert signal.title == "Runway 16 departure EMAS project"
        assert signal.category == "new_installation"
        assert signal.status == "funded"
        assert signal.published is True
        assert signal.source_id is not None
        assert signal.runway_id is not None
        assert signal.manual_year_estimate is None


# 7. Second execution fails closed / no duplicate append.
def test_second_execution_is_idempotent_noop():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)

        _, first_updated = correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))
        signal, second_updated = correct_bgm_signal6_target_year(session, today=date(2026, 9, 7))

        assert first_updated is True
        assert second_updated is False
        assert signal.source_notes.count(CORRECTION_NOTE) == 1
        assert signal.target_year is None


# 8. Wrong precondition (target_year already something else) fails closed.
def test_fails_closed_when_target_year_precondition_differs():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session, target_year=2030)
        try:
            correct_bgm_signal6_target_year(session)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert "target_year" in str(exc)


# 8b. Wrong precondition (source_id differs) fails closed.
def test_fails_closed_when_source_id_precondition_differs():
    Session = session_factory()
    with Session() as session:
        other_source = Source(
            title="Unrelated source", source_type="news", reliability_level="unverified",
            url="https://example.test/unrelated",
        )
        session.add(other_source)
        session.flush()
        _seed_bgm_signal6(session, source=other_source)
        try:
            correct_bgm_signal6_target_year(session)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert "source_id" in str(exc)


# 8c. Wrong precondition (planning_year differs) fails closed.
def test_fails_closed_when_planning_year_precondition_differs():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session, planning_year=2027)
        try:
            correct_bgm_signal6_target_year(session)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert "planning_year" in str(exc)


# 8d. Missing Signal 6 entirely fails closed.
def test_fails_closed_when_signal_missing():
    Session = session_factory()
    with Session() as session:
        try:
            correct_bgm_signal6_target_year(session)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert f"id={SIGNAL_ID}" in str(exc)


# 9. No other Signal is changed.
def test_no_other_signal_changed():
    Session = session_factory()
    with Session() as session:
        _seed_bgm_signal6(session)
        airport2 = Airport(iata_code="XXX", icao_code="KXXX", faa_code="XXX", name="Other Airport", country="USA")
        session.add(airport2)
        session.flush()
        other = Signal(
            airport=airport2, title="Unrelated signal", category="replacement",
            confidence="high", planning_year=2028, target_year=2028, published=True,
        )
        session.add(other)
        session.commit()
        other_id = other.id

        correct_bgm_signal6_target_year(session, today=date(2026, 9, 6))

        untouched = session.get(Signal, other_id)
        assert untouched.target_year == 2028
        assert untouched.planning_year == 2028

        total = session.scalar(select(func.count()).select_from(Signal))
        assert total == 2
