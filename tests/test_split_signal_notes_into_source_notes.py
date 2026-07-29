from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal
from scripts.split_signal_notes_into_source_notes import (
    SIGNAL_IDS_TO_MOVE,
    move_notes_to_source_notes,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_signal(session, *, notes, source_notes=None):
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        airport=airport,
        title="Test signal",
        category="new_installation",
        confidence="high",
        notes=notes,
        source_notes=source_notes,
    )
    session.add(signal)
    session.commit()
    return signal.id


def test_move_transfers_notes_to_source_notes_and_clears_notes():
    Session = session_factory()
    with Session() as session:
        signal_id = _seed_signal(session, notes="Sourced research text.")

        moved = move_notes_to_source_notes(session, signal_ids=(signal_id,))

        assert moved == [signal_id]
        signal = session.get(Signal, signal_id)
        assert signal.notes is None
        assert signal.source_notes == "Sourced research text."


def test_move_skips_a_row_whose_notes_are_already_none():
    Session = session_factory()
    with Session() as session:
        signal_id = _seed_signal(session, notes=None, source_notes="Already moved.")

        moved = move_notes_to_source_notes(session, signal_ids=(signal_id,))

        assert moved == []
        signal = session.get(Signal, signal_id)
        assert signal.source_notes == "Already moved."


def test_move_refuses_to_overwrite_an_existing_source_notes_value():
    Session = session_factory()
    with Session() as session:
        signal_id = _seed_signal(session, notes="New text.", source_notes="Existing text.")

        try:
            move_notes_to_source_notes(session, signal_ids=(signal_id,))
            assert False, "expected SystemExit"
        except SystemExit:
            pass

        # nothing changed
        signal = session.get(Signal, signal_id)
        assert signal.notes == "New text."
        assert signal.source_notes == "Existing text."


def test_move_is_idempotent():
    Session = session_factory()
    with Session() as session:
        signal_id = _seed_signal(session, notes="Sourced research text.")

        move_notes_to_source_notes(session, signal_ids=(signal_id,))
        moved_second_run = move_notes_to_source_notes(session, signal_ids=(signal_id,))

        assert moved_second_run == []


def test_signal_ids_to_move_covers_the_reviewed_67_minus_fty():
    # Signal 5 (FTY) was already moved by hand and is deliberately excluded
    # from this script's list - see the module docstring.
    assert len(SIGNAL_IDS_TO_MOVE) == 66
    assert len(set(SIGNAL_IDS_TO_MOVE)) == 66  # no duplicates
    assert 5 not in SIGNAL_IDS_TO_MOVE
    assert set(SIGNAL_IDS_TO_MOVE) == set(range(1, 68)) - {5}
