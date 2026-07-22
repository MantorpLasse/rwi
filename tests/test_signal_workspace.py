from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Runway, Signal, Source


@pytest.fixture
def workspace(tmp_path):
    database_path = tmp_path / "workspace.sqlite3"
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
        with TestClient(app) as client:
            yield client, testing_session
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def make_signal(title="Terminal runway safety", *, airport=None, runway=None, source=None):
    airport = airport or Airport(
        name="Arlanda Test Airport",
        iata_code="ARN",
        icao_code="ESSA",
        country="Sweden",
    )
    return Signal(
        airport=airport,
        runway=runway,
        source=source,
        title=title,
        category="new_installation",
        status="planned",
        confidence="confirmed",
        planning_year=2028,
        procurement_year=2027,
        probability_score=8.5,
        notes="Safety area upgrade",
        estimated_total_value_usd=Decimal("12500000"),
        estimated_emas_value_usd=Decimal("3200000"),
    )


def test_signal_identity_airport_runway_and_economics_render(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        airport = Airport(name="Arlanda Test Airport", iata_code="ARN", country="Sweden")
        runway = Runway(airport=airport, designation="01L/19R")
        signal = make_signal(airport=airport, runway=runway)
        session.add(signal)
        session.commit()
        signal_id = signal.id

    response = client.get(f"/signals/{signal_id}")

    assert response.status_code == 200
    for text in ("Terminal runway safety", "ARN", "Arlanda Test Airport", "01L/19R"):
        assert text in response.text
    assert "$12,500,000" in response.text
    assert "$3,200,000" in response.text


def test_signal_source_renders(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        source = Source(
            title="Aerodrome safety decision",
            source_type="decision",
            publisher="Swedish Transport Agency",
            url="https://example.test/safety?id=7&format=pdf",
        )
        signal = make_signal(source=source)
        session.add(signal)
        session.commit()
        signal_id = signal.id

    html = client.get(f"/signals/{signal_id}").text

    for text in (
        "Källa",
        "Aerodrome safety decision",
        "Swedish Transport Agency",
        "decision",
    ):
        assert text in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_signal_without_source_shows_empty_state(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        signal = make_signal()
        session.add(signal)
        session.commit()
        signal_id = signal.id

    html = client.get(f"/signals/{signal_id}").text

    assert "Ingen källa kopplad ännu." in html


def test_missing_signal_returns_404(workspace):
    client, _session_factory = workspace
    response = client.get("/signals/999999")
    assert response.status_code == 404


def test_health_airport_and_signal_list_routes_remain_available(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        signal = make_signal()
        session.add(signal)
        session.commit()
        airport_id = signal.airport.id

    assert client.get("/health").status_code == 200
    assert client.get("/airports").status_code == 200
    assert client.get(f"/airports/{airport_id}").status_code == 200
    assert client.get("/signals").status_code == 200
