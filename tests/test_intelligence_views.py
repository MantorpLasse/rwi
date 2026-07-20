from datetime import UTC, date, datetime, timedelta

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
    FindingType,
    Intelligence,
    IntelligenceStatus,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)


@pytest.fixture
def intelligence_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'intelligence-views.sqlite3'}",
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


def add_finding_type(session, key="CURRENT_EMAS"):
    finding_type = FindingType(
        key=key,
        name="Current EMAS",
        description="A current EMAS installation is established by accepted Facts.",
        category="STATUS",
    )
    session.add(finding_type)
    session.flush()
    return finding_type


def add_fact(session, key="airport.emas.product", value="EMASMAX"):
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key}"),
                title=f"Document {key}",
            ),
            observation_type=ObservationType(
                key=key,
                display_label=key,
                description="Intelligence view evidence",
                value_type="raw_text",
            ),
            raw_value=value,
        ),
        status=VerificationStatus.ACCEPTED,
        reviewed_by="Analyst",
    )
    fact = Fact(
        fact_type_key=key,
        subject_type="airport",
        subject_identifier="FAA:JFK",
        accepted_value=value,
        valid_from=date(2026, 1, 1),
        status=FactStatus.ACCEPTED,
        supporting_verifications=[verification],
    )
    session.add(fact)
    session.flush()
    return fact


def finding(finding_type, support, **values):
    return Intelligence(
        finding_type=finding_type,
        title=values.pop("title", "Current EMAS established"),
        summary=values.pop(
            "summary", "Accepted Facts establish a current EMAS installation."
        ),
        status=values.pop("status", IntelligenceStatus.ACTIVE),
        derived_at=values.pop("derived_at", datetime(2026, 7, 20, 12, 30, tzinfo=UTC)),
        supporting_facts=[support],
        **values,
    )


def test_empty_states_and_current_history_navigation(intelligence_app):
    client, _session_factory = intelligence_app

    current = client.get("/intelligence")
    history = client.get("/intelligence?history=all")

    assert current.status_code == history.status_code == 200
    assert "Current Intelligence" in current.text
    assert "No current Intelligence recorded." in current.text
    assert 'href="/intelligence?history=all"' in current.text
    assert 'href="/facts">Browse Facts</a>' in current.text
    assert "Complete Intelligence history" in history.text
    assert "No Intelligence recorded." in history.text
    assert 'href="/intelligence">View current Intelligence</a>' in history.text


def test_default_list_uses_current_repository_logic_and_presents_finding_type(intelligence_app):
    client, session_factory = intelligence_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        first_fact = add_fact(session)
        second_fact = add_fact(session, "airport.emas.system_count", "2")
        instant = datetime(2026, 7, 19, tzinfo=UTC)
        original = finding(
            finding_type,
            first_fact,
            title="Historical finding",
            created_at=instant,
        )
        replacement = finding(
            finding_type,
            first_fact,
            title="Current replacement",
            supersedes=original,
            created_at=instant + timedelta(days=1),
        )
        replacement.supporting_facts.append(second_fact)
        session.add_all([original, replacement])
        session.commit()
        replacement_id = replacement.id

    html = client.get("/intelligence").text
    assert "Current replacement" in html
    assert "Historical finding" not in html
    assert "CURRENT_EMAS" in html
    assert "Current EMAS" in html
    assert "STATUS" in html
    assert "active" in html
    assert "2026-07-20 12:30" in html
    assert f'href="/intelligence/{replacement_id}"' in html
    assert ">2</td>" in html


def test_history_lists_active_superseded_and_archived_intelligence(intelligence_app):
    client, session_factory = intelligence_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        fact = add_fact(session)
        active_original = finding(finding_type, fact, title="Active historical")
        superseded = finding(
            finding_type,
            fact,
            title="Explicitly superseded",
            status=IntelligenceStatus.SUPERSEDED,
            supersedes=active_original,
        )
        archive_original = finding(finding_type, fact, title="Archive predecessor")
        archived = finding(
            finding_type,
            fact,
            title="Archived finding",
            status=IntelligenceStatus.ARCHIVED,
            supersedes=archive_original,
        )
        session.add_all([active_original, superseded, archive_original, archived])
        session.commit()

    html = client.get("/intelligence?history=all").text
    for value in (
        "Active historical",
        "Explicitly superseded",
        "Archived finding",
        "active",
        "superseded",
        "archived",
    ):
        assert value in html


def test_detail_displays_complete_finding_and_multiple_supporting_facts(intelligence_app):
    client, session_factory = intelligence_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        first = add_fact(session)
        second = add_fact(session, "airport.emas.system_count", "2")
        item = finding(
            finding_type,
            first,
            summary="First conclusion line.\nSecond complete conclusion line.",
        )
        item.supporting_facts.append(second)
        session.add(item)
        session.commit()
        intelligence_id = item.id
        fact_ids = (first.id, second.id)

    response = client.get(f"/intelligence/{intelligence_id}")
    html = response.text
    assert response.status_code == 200
    for value in (
        f"Intelligence #{intelligence_id}",
        "CURRENT_EMAS",
        "Current EMAS",
        "A current EMAS installation is established by accepted Facts.",
        "STATUS",
        "First conclusion line.\nSecond complete conclusion line.",
        "active",
        "2026-07-20 12:30",
        "airport.emas.product",
        "airport.emas.system_count",
        "FAA:JFK",
        "EMASMAX",
        "Valid from: 2026-01-01",
        "No lineage recorded.",
    ):
        assert value in html
    for fact_id in fact_ids:
        assert f'href="/facts/{fact_id}"' in html
    for forbidden in ("Create Intelligence", "Edit", "Delete", "Archive"):
        assert forbidden not in html


def test_detail_links_predecessor_and_successor(intelligence_app):
    client, session_factory = intelligence_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        fact = add_fact(session)
        predecessor = finding(finding_type, fact, title="Predecessor")
        successor = finding(
            finding_type,
            fact,
            title="Successor",
            supersedes=predecessor,
        )
        session.add_all([predecessor, successor])
        session.commit()
        predecessor_id, successor_id = predecessor.id, successor.id

    predecessor_html = client.get(f"/intelligence/{predecessor_id}").text
    successor_html = client.get(f"/intelligence/{successor_id}").text
    assert f'href="/intelligence/{successor_id}"' in predecessor_html
    assert f"Successor Intelligence #{successor_id}" in predecessor_html
    assert f'href="/intelligence/{predecessor_id}"' in successor_html
    assert f"Predecessor Intelligence #{predecessor_id}" in successor_html


def test_unknown_navigation_and_fact_traceability_remain_available(intelligence_app):
    client, session_factory = intelligence_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        fact = add_fact(session)
        item = finding(finding_type, fact)
        session.add(item)
        session.commit()
        intelligence_id = item.id
        fact_id = fact.id
        verification_id = fact.supporting_verifications[0].id

    assert client.get("/intelligence/999999").status_code == 404
    assert 'href="/intelligence"' in client.get("/").text

    intelligence_html = client.get(f"/intelligence/{intelligence_id}").text
    fact_response = client.get(f"/facts/{fact_id}")
    verification_response = client.get(f"/verifications/{verification_id}")
    assert f'href="/facts/{fact_id}"' in intelligence_html
    assert fact_response.status_code == 200
    assert f'href="/verifications/{verification_id}"' in fact_response.text
    assert verification_response.status_code == 200
    assert "/observations/" in verification_response.text
    assert client.get("/facts").status_code == 200
