import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal
from scripts.remove_docs_and_field_leaks_from_notes import (
    INSTALLATION_FIXES,
    SIGNAL_FIXES,
    fix_leaks,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_airport(session: Session) -> Airport:
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    return airport


def _seed_signal(session: Session, signal_id: int, source_notes: str) -> None:
    airport = _seed_airport(session)
    session.add(
        Signal(
            id=signal_id,
            airport=airport,
            title="Test signal",
            category="new_installation",
            confidence="high",
            source_notes=source_notes,
        )
    )
    session.commit()


def _seed_installation(session: Session, installation_id: int, notes: str) -> None:
    airport = _seed_airport(session)
    session.add(Installation(id=installation_id, airport=airport, notes=notes))
    session.commit()


@pytest.mark.parametrize("signal_id", sorted(SIGNAL_FIXES))
def test_every_signal_fragment_is_replaced(signal_id):
    Session = session_factory()
    with Session() as session:
        fixes = SIGNAL_FIXES[signal_id]
        seed_text = " ".join(old for old, _ in fixes)
        _seed_signal(session, signal_id, seed_text)

        fixed_signals, fixed_installations = fix_leaks(session)

        assert fixed_signals == [signal_id]
        assert fixed_installations == []
        signal = session.get(Signal, signal_id)
        for old, new in fixes:
            assert old not in signal.source_notes
            if new:
                assert new in signal.source_notes


@pytest.mark.parametrize("installation_id", sorted(INSTALLATION_FIXES))
def test_every_installation_fragment_is_replaced(installation_id):
    Session = session_factory()
    with Session() as session:
        fixes = INSTALLATION_FIXES[installation_id]
        seed_text = " ".join(old for old, _ in fixes)
        _seed_installation(session, installation_id, seed_text)

        fixed_signals, fixed_installations = fix_leaks(session)

        assert fixed_signals == []
        assert fixed_installations == [installation_id]
        installation = session.get(Installation, installation_id)
        for old, new in fixes:
            assert old not in installation.notes
            if new:
                assert new in installation.notes


def test_fix_is_idempotent():
    Session = session_factory()
    with Session() as session:
        signal_id = next(iter(SIGNAL_FIXES))
        installation_id = next(iter(INSTALLATION_FIXES))
        _seed_signal(session, signal_id, " ".join(old for old, _ in SIGNAL_FIXES[signal_id]))
        _seed_installation(
            session, installation_id, " ".join(old for old, _ in INSTALLATION_FIXES[installation_id])
        )

        fix_leaks(session)
        fixed_signals_second, fixed_installations_second = fix_leaks(session)

        assert fixed_signals_second == []
        assert fixed_installations_second == []


def test_fix_touches_no_other_leak_patterns():
    """None of the rewritten fragments should still contain the patterns
    the original leak audit grepped for (docs/, scripts/, source_id,
    install_year=, Installation-rad, databasen)."""
    leak_patterns = ["docs/", "scripts/", "source_id", "install_year=", "Installation-rad", "databasen"]
    for fixes in list(SIGNAL_FIXES.values()) + list(INSTALLATION_FIXES.values()):
        for _, new in fixes:
            for pattern in leak_patterns:
                assert pattern not in new


def test_signal_ids_match_the_six_confirmed_hits():
    assert set(SIGNAL_FIXES) == {6, 49, 55, 58, 59, 60}


def test_installation_ids_match_the_twenty_confirmed_hits():
    assert set(INSTALLATION_FIXES) == {
        17, 22, 23, 24, 43, 71, 83, 84, 98, 100,
        109, 133, 142, 143, 144, 145, 146, 147, 148, 149,
    }
