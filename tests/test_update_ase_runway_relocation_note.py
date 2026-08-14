from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Runway, Signal
from scripts.update_ase_runway_relocation_note import update_ase_signal


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_ase_with_signal(session):
    airport = Airport(iata_code="ASE", icao_code="KASE", faa_code="ASE", name="Aspen/Pitkin County Airport", country="USA")
    runway = Runway(airport=airport, designation="15/33", length_m=2440)
    signal = Signal(
        airport=airport,
        runway=runway,
        title="Runway 15/33 future EMAS at both runway ends",
        category="new_installation",
        confidence="planned",
        status="alp",
        planning_year=2027,
        source_notes="Two EMAS beds are shown in Aspen's adopted future airport layout concept.",
    )
    session.add(signal)
    session.commit()
    return signal.id


def test_update_appends_note_without_touching_confidence():
    Session = session_factory()
    with Session() as session:
        _seed_ase_with_signal(session)

        signal, updated = update_ase_signal(session, today=date(2026, 8, 14))

        assert updated is True
        assert signal.confidence == "planned"
        # old note preserved
        assert "Two EMAS beds are shown" in signal.source_notes
        # new note content
        assert "80 fot västerut" in signal.source_notes
        assert "100 till 150 fot" in signal.source_notes
        assert "575 MUSD" in signal.source_notes
        assert "april 2027" in signal.source_notes
        assert "kvarstår, snarare bekräftad än löst" in signal.source_notes
        assert "aspenairport.com/construction" in signal.source_notes
        assert "pitkincounty.com (2026-05-28)" in signal.source_notes
        assert "simpleflying.com (2026-06-01)" in signal.source_notes
        # private notes field untouched
        assert signal.notes is None


def test_update_does_not_claim_tension_is_resolved():
    Session = session_factory()
    with Session() as session:
        _seed_ase_with_signal(session)

        signal, _ = update_ase_signal(session, today=date(2026, 8, 14))

        assert "löst" in signal.source_notes
        assert "snarare bekräftad än löst" in signal.source_notes


def test_update_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_ase_with_signal(session)

        update_ase_signal(session, today=date(2026, 8, 14))
        signal, updated_second_run = update_ase_signal(session, today=date(2026, 8, 15))

        assert updated_second_run is False
        assert signal.source_notes.count("Uppdatering aug 2026") == 1


def test_update_raises_if_ase_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            update_ase_signal(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_update_raises_if_signal_is_missing():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(iata_code="ASE", icao_code="KASE", name="Aspen/Pitkin County Airport", country="USA"))
        session.commit()
        try:
            update_ase_signal(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
