from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Document, Observation, ObservationType, PublishingSource


@pytest.fixture
def observation_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'observation-views.sqlite3'}",
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


def add_foundation(session, title="FAA EMAS document"):
    document = Document(source=PublishingSource(name="FAA"), title=title)
    observation_type = ObservationType(
        key="airport.emas.product",
        display_label="EMAS product",
        description="Product name extracted from source evidence",
        value_type="raw_text",
    )
    session.add_all([document, observation_type])
    session.flush()
    return document, observation_type


def test_observation_list_empty_state_and_no_write_controls(observation_app):
    client, _session_factory = observation_app
    response = client.get("/observations")

    assert response.status_code == 200
    assert "Inga observationer har registrerats." in response.text
    assert "<form" not in response.text
    assert "Skapa" not in response.text
    assert "Redigera" not in response.text
    assert "Radera" not in response.text


def test_observation_list_displays_rows_links_and_newest_first(observation_app):
    client, session_factory = observation_app
    with session_factory() as session:
        document, observation_type = add_foundation(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        older = Observation(document=document, observation_type=observation_type, raw_value="older raw", created_at=instant)
        newer = Observation(
            document=document,
            observation_type=observation_type,
            raw_value="newer raw",
            extraction_confidence=0.8,
            evidence_locator="Map marker 12",
            created_at=instant + timedelta(days=1),
            supersedes=older,
        )
        session.add_all([older, newer])
        session.commit()
        older_id, newer_id = older.id, newer.id

    response = client.get("/observations")
    html = response.text
    assert response.status_code == 200
    assert html.index("newer raw") < html.index("older raw")
    assert "EMAS product" in html
    assert "FAA EMAS document" in html
    assert "0.8" in html
    assert "Map marker 12" in html
    assert f'href="/observations/{older_id}"' in html
    assert f'href="/observations/{newer_id}"' in html
    assert f"Ersätter #{older_id}" in html


def test_observation_detail_shows_complete_evidence_and_relationships(observation_app):
    client, session_factory = observation_app
    raw = "Complete raw value\nwith an unabridged second line"
    with session_factory() as session:
        document, observation_type = add_foundation(session)
        item = Observation(
            document=document,
            observation_type=observation_type,
            raw_value=raw,
            normalized_value="EMASMAX candidate",
            extraction_confidence=0.75,
            extraction_method="manual extraction",
            extractor_version="analyst-v1",
            evidence_locator="Popup for JFK",
        )
        session.add(item)
        session.commit()
        item_id, document_id = item.id, document.id

    response = client.get(f"/observations/{item_id}")
    html = response.text
    assert response.status_code == 200
    for value in (
        "Complete raw value\nwith an unabridged second line",
        "airport.emas.product",
        "EMAS product",
        "Product name extracted from source evidence",
        "Normaliserad kandidat",
        "EMASMAX candidate",
        "0.75",
        "manual extraction",
        "analyst-v1",
        "Popup for JFK",
        "FAA",
    ):
        assert value in html
    assert f'href="/documents/{document_id}"' in html
    assert 'href="/observations">↑ Upp: Observationer</a>' in html
    assert 'href="#verifications">Nästa: Verifieringar →</a>' in html
    assert 'class="text-break evidence-value"' in html
    assert "None" not in html
    assert "Verifierat värde" not in html
    assert "Godkänn" not in html


def test_detail_links_prior_and_later_corrections(observation_app):
    client, session_factory = observation_app
    with session_factory() as session:
        document, observation_type = add_foundation(session)
        prior = Observation(document=document, observation_type=observation_type, raw_value="prior")
        current = Observation(document=document, observation_type=observation_type, raw_value="current", supersedes=prior)
        later = Observation(document=document, observation_type=observation_type, raw_value="later", supersedes=current)
        session.add_all([prior, current, later])
        session.commit()
        prior_id, current_id, later_id = prior.id, current.id, later.id

    html = client.get(f"/observations/{current_id}").text
    assert f'href="/observations/{prior_id}"' in html
    assert f"Tidigare observation #{prior_id}" in html
    assert f'href="/observations/{later_id}"' in html
    assert f"Senare korrigering #{later_id}" in html


def test_missing_observation_returns_404(observation_app):
    client, _session_factory = observation_app
    assert client.get("/observations/999999").status_code == 404


def test_document_detail_lists_observations_in_oldest_first_order(observation_app):
    client, session_factory = observation_app
    with session_factory() as session:
        document, observation_type = add_foundation(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        later = Observation(document=document, observation_type=observation_type, raw_value="document later", created_at=instant + timedelta(days=1))
        earlier = Observation(document=document, observation_type=observation_type, raw_value="document earlier", created_at=instant)
        session.add_all([later, earlier])
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text
    assert html.index("document earlier") < html.index("document later")
    assert "Relaterade observationer" in html
    assert "/observations/" in html


def test_document_detail_has_observation_empty_state(observation_app):
    client, session_factory = observation_app
    with session_factory() as session:
        document, _observation_type = add_foundation(session)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text
    assert "Inga observationer är kopplade till dokumentet." in html


def test_source_derived_html_is_escaped_on_list_detail_and_document(observation_app):
    client, session_factory = observation_app
    attack = '<script>alert("source")</script>'
    with session_factory() as session:
        document, observation_type = add_foundation(session, title=attack)
        observation_type.display_label = attack
        item = Observation(document=document, observation_type=observation_type, raw_value=attack, evidence_locator=attack)
        session.add(item)
        session.commit()
        item_id, document_id = item.id, document.id

    for path in ("/observations", f"/observations/{item_id}", f"/documents/{document_id}"):
        html = client.get(path).text
        assert attack not in html
        assert "&lt;script&gt;" in html
