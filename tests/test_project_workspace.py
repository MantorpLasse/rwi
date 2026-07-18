from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Document, Project, PublishingSource, Runway, Source


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


def make_project(title="Terminal runway safety", *, airport=None, runway=None):
    airport = airport or Airport(
        name="Arlanda Test Airport",
        iata_code="ARN",
        icao_code="ESSA",
        country="Sweden",
    )
    return Project(
        airport=airport,
        runway=runway,
        title=title,
        project_type="emas",
        status="planned",
        confidence_level="confirmed",
        planning_year=2028,
        procurement_year=2027,
        probability_score=8.5,
        description="Safety area upgrade",
        estimated_total_value_usd=Decimal("12500000"),
        estimated_emas_value_usd=Decimal("3200000"),
    )


def test_project_identity_airport_runway_and_economics_render(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        airport = Airport(name="Arlanda Test Airport", iata_code="ARN", country="Sweden")
        runway = Runway(airport=airport, designation="01L/19R")
        project = make_project(airport=airport, runway=runway)
        session.add(project)
        session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 200
    for text in ("Terminal runway safety", "ARN", "Arlanda Test Airport", "01L/19R"):
        assert text in response.text
    assert "$12,500,000" in response.text
    assert "$3,200,000" in response.text


def test_normalized_document_and_publisher_render_but_legacy_source_does_not(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        project = make_project()
        project.documents.append(
            Document(
                source=PublishingSource(name="Swedish Transport Agency"),
                title="Aerodrome safety decision",
                document_type="decision",
                url="https://example.test/safety?id=7&format=pdf",
                published_date=date(2026, 3, 4),
                accessed_date=date(2026, 7, 18),
                revision="Rev 2",
                document_reference="TSFS-2026-7",
                summary="Approved runway safety improvements.",
                status="active",
            )
        )
        project.sources.append(
            Source(
                title="LEGACY CONTENT MUST STAY HIDDEN",
                source_type="web",
                publisher="Old publisher",
                url="https://legacy.test",
            )
        )
        session.add(project)
        session.commit()
        project_id = project.id

    html = client.get(f"/projects/{project_id}").text

    for text in (
        "Dokument",
        "Aerodrome safety decision",
        "Swedish Transport Agency",
        "decision",
        "2026-03-04",
        "2026-07-18",
        "Rev 2",
        "TSFS-2026-7",
        "Approved runway safety improvements.",
        "active",
    ):
        assert text in html
    assert "LEGACY CONTENT MUST STAY HIDDEN" not in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_empty_normalized_document_state_does_not_fall_back_to_legacy(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        project = make_project()
        project.sources.append(
            Source(title="Hidden legacy row", source_type="web", url="https://legacy.test")
        )
        session.add(project)
        session.commit()
        project_id = project.id

    html = client.get(f"/projects/{project_id}").text

    assert "Inga normaliserade dokument är kopplade till projektet." in html
    assert "Äldre källposter kan fortfarande vänta på normalisering." in html
    assert "Hidden legacy row" not in html


@pytest.mark.parametrize(
    ("status", "badge_class"),
    [
        ("active", "text-bg-success"),
        ("incomplete", "text-bg-warning"),
        ("superseded", "text-bg-secondary"),
        ("withdrawn", "text-bg-danger"),
        ("unavailable", "text-bg-dark"),
    ],
)
def test_each_document_status_has_visible_text_and_distinct_badge(workspace, status, badge_class):
    client, session_factory = workspace
    with session_factory() as session:
        project = make_project(title=f"Status {status}")
        project.documents.append(
            Document(source=PublishingSource(name="Publisher"), title="Status document", status=status)
        )
        session.add(project)
        session.commit()
        project_id = project.id

    html = client.get(f"/projects/{project_id}").text

    assert f">{status}</span>" in html
    assert badge_class in html
    if status == "incomplete":
        assert "Länken kan leda till utgivarens startsida" in html
        assert "Verifiera metadata" in html


def test_nullable_document_metadata_renders_without_empty_labels(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        project = make_project()
        project.documents.append(
            Document(source=PublishingSource(name="Publisher"), title="Minimal document", status="active")
        )
        session.add(project)
        session.commit()
        project_id = project.id

    html = client.get(f"/projects/{project_id}").text

    assert "Minimal document" in html
    for absent_label in ("Dokumenttyp", "Publicerad", "Hämtad", "Revision", "Dokumentreferens", "Öppna dokument"):
        assert absent_label not in html


def test_one_document_appears_in_two_project_workspaces(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        shared = Document(
            source=PublishingSource(name="FAA"),
            title="Shared airport report",
            status="active",
        )
        first = make_project("First project")
        second = make_project("Second project")
        shared.projects.extend([first, second])
        session.add(shared)
        session.commit()
        project_ids = (first.id, second.id)

    for project_id in project_ids:
        assert "Shared airport report" in client.get(f"/projects/{project_id}").text


def test_missing_project_returns_404(workspace):
    client, _session_factory = workspace
    response = client.get("/projects/999999")
    assert response.status_code == 404


def test_health_airport_and_project_list_routes_remain_available(workspace):
    client, session_factory = workspace
    with session_factory() as session:
        project = make_project()
        session.add(project)
        session.commit()
        airport_id = project.airport.id

    assert client.get("/health").status_code == 200
    assert client.get("/airports").status_code == 200
    assert client.get(f"/airports/{airport_id}").status_code == 200
    assert client.get("/projects").status_code == 200
