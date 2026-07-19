import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Document,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)


@pytest.fixture
def creation_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-verification.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def add_observation(session, key="airport.emas.product"):
    observation = Observation(
        document=Document(source=PublishingSource(name="FAA"), title="FAA evidence"),
        observation_type=ObservationType(
            key=key,
            display_label="EMAS product",
            description="Product claim",
            value_type="raw_text",
        ),
        raw_value="EMASMAX source claim",
    )
    session.add(observation)
    session.commit()
    return observation


def valid_data(**overrides):
    data = {
        "status": "ACCEPTED",
        "reviewed_by": "Safety reviewer",
        "confidence": "0.85",
        "comment": "Source context supports the claim.",
    }
    data.update(overrides)
    return data


def verification_count(session_factory):
    with session_factory() as session:
        return session.scalar(select(func.count(Verification.id)))


def test_get_form_identifies_observation_and_lists_allowed_statuses(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.get(f"/observations/{observation_id}/verifications/new")
    assert response.status_code == 200
    assert f"Observation #{observation_id}" in response.text
    assert "EMASMAX source claim" not in response.text
    for status in VerificationStatus:
        assert f'value="{status.name}"' in response.text
        assert f">{status.name}</option>" in response.text


def test_valid_post_creates_exactly_one_verification_and_redirects(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id
        original_raw = observation.raw_value

    response = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(),
        follow_redirects=False,
    )
    assert response.status_code == 303

    with session_factory() as session:
        items = session.scalars(select(Verification)).all()
        assert len(items) == 1
        verification = items[0]
        assert response.headers["location"] == f"/verifications/{verification.id}"
        assert verification.observation_id == observation_id
        assert verification.status is VerificationStatus.ACCEPTED
        assert verification.reviewed_by == "Safety reviewer"
        assert verification.confidence == 0.85
        assert verification.comment == "Source context supports the claim."
        assert session.get(Observation, observation_id).raw_value == original_raw


def test_empty_optional_fields_become_null(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(reviewed_by=" ", confidence="", comment=""),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        verification = session.scalar(select(Verification))
        assert verification.reviewed_by is None
        assert verification.confidence is None
        assert verification.comment is None


@pytest.mark.parametrize("confidence", ["0.0", "1.0"])
def test_confidence_boundaries_are_accepted(creation_app, confidence):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(confidence=confidence),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        assert session.scalar(select(Verification.confidence)) == float(confidence)


@pytest.mark.parametrize(
    "confidence",
    ["malformed", "nan", "inf", "-inf", "-0.01", "1.01"],
)
def test_invalid_confidence_redisplays_values_and_creates_nothing(
    creation_app, confidence
):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(confidence=confidence),
    )
    assert response.status_code == 422
    assert "Verifieringskonfidens måste" in response.text
    assert f'value="{confidence}"' in response.text
    assert "Safety reviewer" in response.text
    assert "Source context supports the claim." in response.text
    assert verification_count(session_factory) == 0


@pytest.mark.parametrize("status", ["", "APPROVED", "accepted", "UNKNOWN"])
def test_invalid_status_is_rejected_without_creation(creation_app, status):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(status=status),
    )
    assert response.status_code == 422
    assert "Välj en giltig verifieringsstatus." in response.text
    assert verification_count(session_factory) == 0


def test_missing_observation_returns_404_for_get_and_post(creation_app):
    client, session_factory = creation_app
    assert client.get("/observations/999999/verifications/new").status_code == 404
    assert (
        client.post(
            "/observations/999999/verifications/new", data=valid_data()
        ).status_code
        == 404
    )
    assert verification_count(session_factory) == 0


def test_posted_observation_id_cannot_override_route_observation(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        route_observation = add_observation(session)
        other_observation = add_observation(session, key="airport.emas.other")
        route_id, other_id = route_observation.id, other_observation.id

    data = valid_data()
    data["observation_id"] = str(other_id)
    response = client.post(
        f"/observations/{route_id}/verifications/new",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        assert session.scalar(select(Verification.observation_id)) == route_id


def test_every_successful_post_appends_a_new_review_and_history_remains_visible(
    creation_app,
):
    client, session_factory = creation_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    first = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(status="UNDECIDED", comment="First opinion"),
        follow_redirects=False,
    )
    second = client.post(
        f"/observations/{observation_id}/verifications/new",
        data=valid_data(status="REJECTED", comment="Later opinion"),
        follow_redirects=False,
    )
    assert first.status_code == second.status_code == 303
    assert verification_count(session_factory) == 2

    history = client.get(f"/observations/{observation_id}/verifications")
    observation_page = client.get(f"/observations/{observation_id}")
    assert history.status_code == observation_page.status_code == 200
    assert "First opinion" in history.text
    assert "Later opinion" in history.text
    assert f'href="/observations/{observation_id}/verifications/new"' in history.text
    assert (
        f'href="/observations/{observation_id}/verifications/new"'
        in observation_page.text
    )
