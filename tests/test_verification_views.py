from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
def verification_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'verification-views.sqlite3'}",
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


def add_observation(session, type_key="airport.emas.product"):
    observation = Observation(
        document=Document(
            source=PublishingSource(name="FAA"),
            title="FAA EMAS evidence",
        ),
        observation_type=ObservationType(
            key=type_key,
            display_label="EMAS product",
            description="Product claim",
            value_type="raw_text",
        ),
        raw_value="EMASMAX source claim",
    )
    session.add(observation)
    session.commit()
    return observation


def test_verification_list_empty_history_and_observation_navigation(verification_app):
    client, session_factory = verification_app
    with session_factory() as session:
        observation = add_observation(session)
        observation_id = observation.id

    response = client.get(f"/observations/{observation_id}/verifications")
    assert response.status_code == 200
    assert "Inga verifieringar har registrerats." in response.text
    assert f'href="/observations/{observation_id}"' in response.text
    assert "<form" not in response.text


def test_verification_list_is_newest_first_and_links_to_details(verification_app):
    client, session_factory = verification_app
    with session_factory() as session:
        observation = add_observation(session)
        instant = datetime(2026, 7, 1, tzinfo=UTC)
        older = Verification(
            observation=observation,
            status=VerificationStatus.UNDECIDED,
            reviewed_at=instant,
            reviewed_by="Older reviewer",
            comment="Older comment that remains visible",
        )
        newer = Verification(
            observation=observation,
            status=VerificationStatus.ACCEPTED,
            reviewed_at=instant + timedelta(days=1),
            reviewed_by="Newer reviewer",
            confidence=0.9,
            comment="Newer comment that remains visible",
        )
        session.add_all([older, newer])
        session.commit()
        observation_id, older_id, newer_id = observation.id, older.id, newer.id

    html = client.get(f"/observations/{observation_id}/verifications").text
    assert html.index("Newer reviewer") < html.index("Older reviewer")
    assert "accepted" in html
    assert "undecided" in html
    assert "0.9" in html
    assert "Newer comment" in html
    assert f'href="/verifications/{newer_id}"' in html
    assert f'href="/verifications/{older_id}"' in html


def test_verification_detail_displays_review_and_both_navigation_links(verification_app):
    client, session_factory = verification_app
    with session_factory() as session:
        observation = add_observation(session)
        verification = Verification(
            observation=observation,
            status=VerificationStatus.REJECTED,
            reviewed_at=datetime(2026, 7, 19, 12, 30, tzinfo=UTC),
            reviewed_by="Safety reviewer",
            confidence=0.65,
            comment="The source claim is contradicted.\nSecond review line.",
        )
        session.add(verification)
        session.commit()
        observation_id, verification_id = observation.id, verification.id

    response = client.get(f"/verifications/{verification_id}")
    html = response.text
    assert response.status_code == 200
    for value in (
        "rejected",
        "Safety reviewer",
        "2026-07-19",
        "0.65",
        "The source claim is contradicted.\nSecond review line.",
        "EMASMAX source claim",
    ):
        assert value in html
    assert f'href="/observations/{observation_id}"' in html
    assert f'href="/observations/{observation_id}/verifications"' in html
    for forbidden in ("Redigera", "Radera", "Godkänn", "Avvisa"):
        assert forbidden not in html


def test_verification_detail_formats_null_values_without_literal_none(verification_app):
    client, session_factory = verification_app
    with session_factory() as session:
        observation = add_observation(session)
        verification = Verification(
            observation=observation,
            status=VerificationStatus.PENDING,
        )
        session.add(verification)
        session.commit()
        verification_id = verification.id

    html = client.get(f"/verifications/{verification_id}").text
    assert "None" not in html
    assert html.count("—") >= 2
    assert "Kommentar" in html


def test_observation_detail_shows_history_summary_or_empty_state(verification_app):
    client, session_factory = verification_app
    with session_factory() as session:
        empty_observation = add_observation(session)
        empty_id = empty_observation.id

    empty_html = client.get(f"/observations/{empty_id}").text
    assert "Inga verifieringar har registrerats." in empty_html
    assert "Visa verifieringshistorik" not in empty_html

    with session_factory() as session:
        reviewed_observation = add_observation(
            session, type_key="airport.emas.product.reviewed"
        )
        verification = Verification(
            observation=reviewed_observation,
            status=VerificationStatus.ACCEPTED,
            reviewed_by="Observation reviewer",
            confidence=1.0,
        )
        session.add(verification)
        session.commit()
        observation_id, verification_id = reviewed_observation.id, verification.id

    html = client.get(f"/observations/{observation_id}").text
    assert "accepted" in html
    assert "Observation reviewer" in html
    assert "1.0" in html
    assert f'href="/verifications/{verification_id}"' in html
    assert f'href="/observations/{observation_id}/verifications"' in html


def test_missing_observation_and_verification_return_404(verification_app):
    client, _session_factory = verification_app
    assert client.get("/observations/999999/verifications").status_code == 404
    assert client.get("/verifications/999999").status_code == 404
