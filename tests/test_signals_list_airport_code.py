from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Incident, Signal


@pytest.fixture
def client_factory(tmp_path):
    database_path = tmp_path / "airport-code.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), testing_session
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_incident_triggered_signal_shows_faa_code_not_none_in_signals_list(client_factory):
    client, session_factory = client_factory
    with session_factory() as session:
        # Mirrors airports imported by scripts/import_faa_csv.py: no IATA/ICAO,
        # only the FAA-assigned identifier.
        airport = Airport(name="Bob Hope", faa_code="BUR", country="USA")
        session.add(airport)
        session.flush()
        session.add(
            Incident(
                airport=airport,
                incident_date=date(2017, 4, 1),
                incident_type="overrun",
            )
        )
        session.commit()

    with client:
        response = client.get("/signals")

    assert response.status_code == 200
    assert "Bob Hope" in response.text
    assert "EMAS-ersättning väntas efter incident" in response.text
    assert "BUR" in response.text
    assert ">None<" not in response.text


def test_manually_created_signal_still_shows_iata_code(client_factory):
    client, session_factory = client_factory
    with session_factory() as session:
        airport = Airport(name="Aspen/Pitkin County Airport", iata_code="ASE", icao_code="KASE", country="USA")
        session.add(airport)
        session.flush()
        session.add(
            Signal(
                airport=airport,
                title="Manually entered signal",
                category="new_installation",
                confidence="planned",
            )
        )
        session.commit()

    with client:
        response = client.get("/signals")

    assert response.status_code == 200
    assert "ASE" in response.text
    assert ">None<" not in response.text


def test_dashboard_does_not_show_none_for_faa_code_only_airport(client_factory):
    client, session_factory = client_factory
    with session_factory() as session:
        airport = Airport(name="Bob Hope", faa_code="BUR", country="USA")
        session.add(airport)
        session.flush()
        session.add(
            Signal(
                airport=airport,
                title="High priority signal",
                category="new_installation",
                confidence="confirmed",
                probability_score=9.5,
            )
        )
        session.commit()

    with client:
        response = client.get("/")

    assert response.status_code == 200
    assert "BUR" in response.text
    assert ">None<" not in response.text
