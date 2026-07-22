from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Incident


@pytest.fixture
def client_factory(tmp_path):
    database_path = tmp_path / "status-score.sqlite3"
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


def test_incident_triggered_signal_shows_a_real_status_badge_and_a_score(client_factory):
    """Reproduces the reported bug: an empty status badge sitting right next to
    the confidence cell used to read as if "high" were the status."""
    client, session_factory = client_factory
    with session_factory() as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
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
    html = response.text
    assert "Replacement expected after incident" in html

    # The status badge must contain real text, not be empty next to "high".
    assert '<span class="badge text-bg-secondary">identified</span>' in html
    # And the score column must show a number, not a bare dash.
    assert "8.0" in html


def test_keyword_rule_signal_shows_a_real_status_badge_and_a_score(client_factory):
    from app.models import Source
    from app.services import add_source_and_flag_keywords

    client, session_factory = client_factory
    with session_factory() as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()
        source = Source(
            title="Master Plan mentions EMAS upgrade",
            source_type="master_plan",
            url="https://example.test/plan.pdf",
        )
        add_source_and_flag_keywords(session, airport=airport, source=source)
        session.commit()

    with client:
        response = client.get("/signals")

    assert response.status_code == 200
    html = response.text
    assert '<span class="badge text-bg-secondary">identified</span>' in html
    assert "3.0" in html
