import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from scripts.graduate_signal_to_installation import ensure_signal_installation_id_column, graduate


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_wlg_style_signal(session: Session) -> dict:
    airport = Airport(iata_code="WLG", icao_code="NZWN", name="Wellington International Airport", country="New Zealand")
    session.add(airport)
    session.flush()
    source = Source(title="EMAS - new runway buffer zones", source_type="news", url="https://example.test/wlg", reliability_level="official")
    session.add(source)
    session.flush()
    signal = Signal(
        airport=airport,
        source=source,
        title="Wellington EMAS-order",
        category="new_installation",
        confidence="high",
        confirmed_vendor="Runway Safe",
        notes="Order signed. Buffer zones +143m/+37m confirmed.",
    )
    session.add(signal)
    session.commit()
    return {"airport_id": airport.id, "source_id": source.id, "signal_id": signal.id}


def test_ensure_signal_installation_id_column_adds_to_old_style_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE signals (id INTEGER PRIMARY KEY, airport_id INTEGER NOT NULL, "
                "title VARCHAR(250) NOT NULL, category VARCHAR(50) NOT NULL, confidence VARCHAR(30) NOT NULL)"
            )
        )
    ensure_signal_installation_id_column(engine)
    ensure_signal_installation_id_column(engine)  # must not raise the second time

    columns = {c["name"] for c in inspect(engine).get_columns("signals")}
    assert "installation_id" in columns


def test_graduate_creates_installation_and_marks_signal_completed():
    Session = session_factory()
    with Session() as session:
        ids = _seed_wlg_style_signal(session)

        installation = graduate(session, ids["signal_id"], install_type="EMAS", install_year=2026)

        assert installation.airport_id == ids["airport_id"]
        assert installation.source_id == ids["source_id"]  # same official source, not a new one
        assert installation.type == "EMAS"
        assert installation.install_year == 2026
        assert installation.status == "active"
        assert installation.confirmed_vendor == "Runway Safe"
        assert installation.notes == "Order signed. Buffer zones +143m/+37m confirmed."

        signal = session.get(Signal, ids["signal_id"])
        assert signal.status == "completed"
        assert signal.installation_id == installation.id


def test_graduate_refuses_to_run_twice():
    Session = session_factory()
    with Session() as session:
        ids = _seed_wlg_style_signal(session)
        graduate(session, ids["signal_id"], install_type="EMAS", install_year=2026)

        with pytest.raises(SystemExit):
            graduate(session, ids["signal_id"], install_type="EMAS", install_year=2026)

        # still exactly one Installation - the guard didn't let a duplicate through
        assert len(session.query(Installation).all()) == 1


def test_graduate_raises_for_unknown_signal_id():
    Session = session_factory()
    with Session() as session:
        with pytest.raises(SystemExit):
            graduate(session, 999, install_type="EMAS", install_year=2026)
