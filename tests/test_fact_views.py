from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Document,
    Fact,
    FactStatus,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)


@pytest.fixture
def fact_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fact-views.sqlite3'}",
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


def add_verification(session, key, reviewer=None, confidence=None):
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key}"),
                title=f"Document {key}",
            ),
            observation_type=ObservationType(
                key=key,
                display_label=key,
                description="Fact view evidence",
                value_type="raw_text",
            ),
            raw_value=f"Raw evidence {key}",
        ),
        status=VerificationStatus.ACCEPTED,
        reviewed_by=reviewer,
        confidence=confidence,
    )
    session.add(verification)
    session.flush()
    return verification


def fact(support, **values):
    return Fact(
        fact_type_key=values.pop("fact_type_key", "airport.emas.product"),
        subject_type=values.pop("subject_type", "airport"),
        subject_identifier=values.pop("subject_identifier", "FAA:JFK"),
        accepted_value=values.pop("accepted_value", "EMASMAX"),
        status=values.pop("status", FactStatus.ACCEPTED),
        supporting_verifications=[support],
        **values,
    )


def test_fact_list_empty_states_and_mode_navigation(fact_app):
    client, _session_factory = fact_app
    current = client.get("/facts")
    history = client.get("/facts?history=all")

    assert current.status_code == history.status_code == 200
    assert "Current Facts" in current.text
    assert "No current Facts recorded." in current.text
    assert 'href="/facts?history=all"' in current.text
    assert "Complete Fact history" in history.text
    assert "No Facts recorded." in history.text
    assert 'href="/facts"' in history.text


def test_default_list_shows_only_current_facts(fact_app):
    client, session_factory = fact_app
    with session_factory() as session:
        support = add_verification(session, "test.current")
        original = fact(support, accepted_value="historical value")
        replacement = fact(
            support,
            accepted_value="current replacement",
            supersedes=original,
        )
        independent = fact(
            support,
            subject_identifier="FAA:ATL",
            accepted_value="independent current",
        )
        session.add_all([original, replacement, independent])
        session.commit()
        replacement_id, independent_id = replacement.id, independent.id

    response = client.get("/facts")
    html = response.text
    assert response.status_code == 200
    assert "Current Facts" in html
    assert "current replacement" in html
    assert "independent current" in html
    assert "historical value" not in html
    assert f'href="/facts/{replacement_id}"' in html
    assert f'href="/facts/{independent_id}"' in html


def test_history_query_displays_accepted_retired_and_superseded_rows(fact_app):
    client, session_factory = fact_app
    with session_factory() as session:
        support = add_verification(session, "test.history")
        original = fact(
            support,
            accepted_value="accepted historical",
            valid_from=date(2020, 1, 1),
            valid_to=date(2025, 12, 31),
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        retirement = fact(
            support,
            status=FactStatus.RETIRED,
            accepted_value="retired historical",
            supersedes=original,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add_all([original, retirement])
        session.commit()

    html = client.get("/facts?history=all").text
    assert "Complete Fact history" in html
    assert html.index("accepted historical") < html.index("retired historical")
    assert "accepted" in html
    assert "retired" in html
    assert "2020-01-01" in html
    assert "2025-12-31" in html


def test_fact_detail_displays_all_fields_and_multiple_supports(fact_app):
    client, session_factory = fact_app
    with session_factory() as session:
        first = add_verification(
            session, "test.detail.first", reviewer="Named reviewer", confidence=0.9
        )
        second = add_verification(session, "test.detail.second")
        item = fact(
            first,
            fact_type_key="airport.emas.system_count",
            subject_type="airport",
            subject_identifier="FAA:BOS",
            accepted_value="2\naccepted systems",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        item.supporting_verifications.append(second)
        session.add(item)
        session.commit()
        fact_id = item.id
        verification_ids = (first.id, second.id)

    response = client.get(f"/facts/{fact_id}")
    html = response.text
    assert response.status_code == 200
    for value in (
        f"Fact #{fact_id}",
        "airport.emas.system_count",
        "airport",
        "FAA:BOS",
        "2\naccepted systems",
        "accepted",
        "2026-01-01",
        "2026-12-31",
        "Named reviewer",
        "0.9",
    ):
        assert value in html
    assert html.count("—") >= 2
    for verification_id in verification_ids:
        assert f'href="/verifications/{verification_id}"' in html
    for forbidden in ("Create Fact", "Edit", "Delete", "Promote", "Retire"):
        assert forbidden not in html


def test_fact_lineage_links_predecessor_and_successor(fact_app):
    client, session_factory = fact_app
    with session_factory() as session:
        support = add_verification(session, "test.lineage")
        predecessor = fact(support, accepted_value="first")
        successor = fact(support, accepted_value="second", supersedes=predecessor)
        session.add_all([predecessor, successor])
        session.commit()
        predecessor_id, successor_id = predecessor.id, successor.id

    predecessor_html = client.get(f"/facts/{predecessor_id}").text
    successor_html = client.get(f"/facts/{successor_id}").text
    assert f'href="/facts/{successor_id}"' in predecessor_html
    assert f"Superseding Fact #{successor_id}" in predecessor_html
    assert f'href="/facts/{predecessor_id}"' in successor_html
    assert f"Superseded Fact #{predecessor_id}" in successor_html


def test_traceability_continues_through_existing_verification_and_observation_pages(
    fact_app,
):
    client, session_factory = fact_app
    with session_factory() as session:
        support = add_verification(session, "test.traceability")
        item = fact(support)
        session.add(item)
        session.commit()
        fact_id, verification_id = item.id, support.id
        observation_id = support.observation.id

    fact_html = client.get(f"/facts/{fact_id}").text
    verification_response = client.get(f"/verifications/{verification_id}")
    observation_response = client.get(f"/observations/{observation_id}")
    assert f'href="/verifications/{verification_id}"' in fact_html
    assert verification_response.status_code == 200
    assert f'href="/observations/{observation_id}"' in verification_response.text
    assert observation_response.status_code == 200
    assert "/documents/" in observation_response.text


def test_unknown_fact_returns_404_and_primary_navigation_links_facts(fact_app):
    client, _session_factory = fact_app
    response = client.get("/facts/999999")
    assert response.status_code == 404
    assert 'href="/facts"' in client.get("/").text
