from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Signal
from scripts.annotate_signal import annotate, append_note, ensure_manual_year_estimate_column, parse_args


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_signal(session, **kwargs):
    airport = Airport(name="Manchester-Boston Regional Airport", faa_code="MHT", country="USA")
    signal = Signal(
        airport=airport,
        title="Runway 6 departure-end EMAS replacement",
        category="replacement",
        confidence="confirmed",
        **kwargs,
    )
    session.add(signal)
    session.commit()
    return signal.id


def test_append_note_preserves_prior_notes_and_stamps_date():
    result = append_note("Two EMAS beds planned.", "Bekräftat via addendum.", on=date(2026, 7, 24))
    assert result == "Two EMAS beds planned.\n[2026-07-24] Bekräftat via addendum."


def test_append_note_on_empty_notes_has_no_leading_newline():
    result = append_note(None, "First note.", on=date(2026, 7, 24))
    assert result == "[2026-07-24] First note."


def test_annotate_appends_note_and_sets_estimated_year_without_touching_official_fields():
    Session = session_factory()
    with Session() as session:
        signal_id = _make_signal(session, notes="Official summary.", planning_year=2026, target_year=None)

        signal = annotate(
            session,
            signal_id,
            note="My hunch based on a bidding addendum.",
            estimated_year=2027,
            today=date(2026, 7, 24),
        )

        assert signal.notes == "Official summary.\n[2026-07-24] My hunch based on a bidding addendum."
        assert signal.manual_year_estimate == 2027
        # Official fields are untouched.
        assert signal.planning_year == 2026
        assert signal.target_year is None


def test_annotate_can_set_only_the_estimated_year():
    Session = session_factory()
    with Session() as session:
        signal_id = _make_signal(session, notes="Official summary.")

        signal = annotate(session, signal_id, note=None, estimated_year=2028)

        assert signal.notes == "Official summary."
        assert signal.manual_year_estimate == 2028


def test_annotate_raises_for_unknown_signal_id():
    Session = session_factory()
    with Session() as session:
        try:
            annotate(session, 999, note="x", estimated_year=None)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_parse_args_requires_note_or_estimated_year():
    try:
        parse_args(["--id", "1"])
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_parse_args_accepts_note_only():
    args = parse_args(["--id", "2", "--note", "text"])
    assert args.signal_id == 2
    assert args.note == "text"
    assert args.estimated_year is None


def test_ensure_manual_year_estimate_column_adds_it_to_an_old_style_table():
    """Simulates the real production DB: a signals table predating manual_year_estimate."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE signals ("
                "id INTEGER PRIMARY KEY, airport_id INTEGER NOT NULL, "
                "title VARCHAR(250) NOT NULL, category VARCHAR(50) NOT NULL, "
                "confidence VARCHAR(30) NOT NULL"
                ")"
            )
        )

    ensure_manual_year_estimate_column(engine)
    ensure_manual_year_estimate_column(engine)  # must not raise the second time

    with Session(engine) as session:
        session.execute(
            text(
                "INSERT INTO signals (airport_id, title, category, confidence, manual_year_estimate) "
                "VALUES (1, 't', 'replacement', 'confirmed', 2027)"
            )
        )
        value = session.execute(text("SELECT manual_year_estimate FROM signals")).scalar_one()
        assert value == 2027
