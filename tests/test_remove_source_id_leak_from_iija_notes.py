from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal
from scripts.remove_source_id_leak_from_iija_notes import (
    NEW_FRAGMENT,
    OLD_FRAGMENT,
    SIGNAL_IDS,
    fix_source_id_leak,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(session: Session) -> int:
    # fix_source_id_leak() targets a hardcoded ID allowlist (SIGNAL_IDS), not
    # content matching - give the seeded row one of those IDs explicitly so
    # the script actually finds it.
    signal_id = SIGNAL_IDS[0]
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        id=signal_id,
        airport=airport,
        title="Test signal",
        category="new_installation",
        confidence="high",
        source_notes=(
            '[2026-07-25] IIJA-bidrag (Announcement 4, FY2026, iija:2026:4:TST): '
            '"Construct/Extend Safety Area", $1,000,000. https://example.test/a4.pdf'
            + OLD_FRAGMENT
        ),
    )
    session.add(signal)
    session.commit()
    return signal.id


def test_fix_replaces_the_internal_fragment_with_reader_facing_text():
    Session = session_factory()
    with Session() as session:
        signal_id = _seed(session)

        fixed = fix_source_id_leak(session)

        assert fixed == [signal_id]
        signal = session.get(Signal, signal_id)
        assert OLD_FRAGMENT not in signal.source_notes
        assert "source_id" not in signal.source_notes
        assert NEW_FRAGMENT in signal.source_notes
        # substantial info preserved
        assert "$1,000,000" in signal.source_notes
        assert "https://example.test/a4.pdf" in signal.source_notes


def test_fix_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed(session)

        fix_source_id_leak(session)
        fixed_second_run = fix_source_id_leak(session)

        assert fixed_second_run == []


def test_signal_ids_matches_the_three_confirmed_hits():
    assert set(SIGNAL_IDS) == {3, 45, 47}
