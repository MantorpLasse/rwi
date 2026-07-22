import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Signal


@pytest.fixture
def client_with_data(tmp_path):
    database_path = tmp_path / "filters.sqlite3"
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

    with testing_session() as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()
        session.add(
            Signal(
                airport=airport,
                title="Test signal",
                category="new_installation",
                confidence="confirmed",
                planning_year=2027,
                probability_score=7.5,
                status="planned",
            )
        )
        session.commit()

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.mark.parametrize(
    "query",
    [
        "?year=&min_score=",
        "?year=&min_score=&status=&country=&q=",
        "?min_score=",
        "?year=",
    ],
)
def test_signals_list_does_not_crash_on_blank_numeric_filters(client_with_data, query):
    response = client_with_data.get(f"/signals{query}")
    assert response.status_code == 200
    assert "Test signal" in response.text


@pytest.mark.parametrize(
    "query",
    [
        "?year=not-a-number",
        "?min_score=not-a-number",
        "?year=99999",  # outside the ge/le range the field used to enforce
        "?min_score=-5",
        "?min_score=999",
    ],
)
def test_signals_list_does_not_crash_on_malformed_or_out_of_range_numeric_filters(
    client_with_data, query
):
    response = client_with_data.get(f"/signals{query}")
    assert response.status_code == 200


def test_signals_list_still_applies_a_valid_year_filter(client_with_data):
    matching = client_with_data.get("/signals?year=2027")
    assert matching.status_code == 200
    assert "Test signal" in matching.text

    non_matching = client_with_data.get("/signals?year=2099")
    assert non_matching.status_code == 200
    assert "Test signal" not in non_matching.text


def test_signals_list_still_applies_a_valid_min_score_filter(client_with_data):
    matching = client_with_data.get("/signals?min_score=5")
    assert matching.status_code == 200
    assert "Test signal" in matching.text

    non_matching = client_with_data.get("/signals?min_score=9")
    assert non_matching.status_code == 200
    assert "Test signal" not in non_matching.text


@pytest.mark.parametrize("query", ["?min_score=", "?min_score=not-a-number", "?min_score=999"])
def test_api_signals_does_not_crash_on_blank_or_bad_min_score(client_with_data, query):
    response = client_with_data.get(f"/api/signals{query}")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Test signal"


def test_api_signals_still_applies_a_valid_min_score_filter(client_with_data):
    response = client_with_data.get("/api/signals?min_score=9")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("query", ["?q=&country=", "?country=", "?q="])
def test_airports_list_does_not_crash_on_blank_string_filters(client_with_data, query):
    response = client_with_data.get(f"/airports{query}")
    assert response.status_code == 200
    assert "Test Airport" in response.text
