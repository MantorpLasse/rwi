from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Airport, Document, Project, PublishingSource, Runway, Source


@pytest.fixture
def document_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-detail.sqlite3'}",
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


def make_project(title, airport, runway=None):
    return Project(
        airport=airport,
        runway=runway,
        title=title,
        project_type="safety",
        status="planned",
        confidence_level="confirmed",
        probability_score=7.0,
    )


def test_document_detail_renders_normalized_metadata_and_safe_external_links(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(
                name="European Aviation Publisher",
                source_type="authority",
                homepage_url="https://publisher.example/home",
            ),
            title="Runway safety publication",
            document_type="technical report",
            url="https://documents.example/report.pdf",
            published_date=date(2026, 1, 5),
            accessed_date=date(2026, 7, 18),
            revision="Revision B",
            document_reference="EAP-42",
            summary="Technical findings for the runway project.",
            status="active",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    response = client.get(f"/documents/{document_id}")

    assert response.status_code == 200
    for text in (
        "Runway safety publication",
        "European Aviation Publisher",
        "authority",
        "technical report",
        "2026-01-05",
        "2026-07-18",
        "Revision B",
        "EAP-42",
        "Technical findings for the runway project.",
        "active",
    ):
        assert text in response.text
    assert 'href="https://documents.example/report.pdf" target="_blank" rel="noopener"' in response.text
    assert 'href="https://publisher.example/home" target="_blank" rel="noopener"' in response.text
    assert 'aria-label="Arbetsflöde"' in response.text
    assert 'href="/documents">↑ Upp: Dokument</a>' in response.text
    assert 'href="#observations">Nästa: Observationer →</a>' in response.text


def test_missing_optional_metadata_has_no_empty_labels(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(name="Minimal Publisher"),
            title="Minimal document",
            status="active",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text

    assert "Minimal document" in html
    for label in (
        "Dokumenttyp",
        "Publicerad",
        "Hämtad",
        "Revision",
        "Dokumentreferens",
        "Sammanfattning",
        "Öppna originaldokument",
        "Utgivarens webbplats",
    ):
        assert label not in html


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
def test_allowed_statuses_render_as_visible_badges(document_app, status, badge_class):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(name="Status Publisher"),
            title=f"Document {status}",
            status=status,
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text

    assert f">{status}</span>" in html
    assert badge_class in html
    if status == "incomplete":
        assert "Länken kan leda till utgivarens startsida" in html
        assert "Verifiera titel, dokumentreferens och revision" in html


def test_document_displays_two_related_projects_with_airport_and_runway_context(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        first_airport = Airport(name="Alpha Airport", iata_code="AAA", country="Sweden")
        first_runway = Runway(airport=first_airport, designation="01/19")
        first = make_project("Alpha safety project", first_airport, first_runway)
        second_airport = Airport(name="Bravo Airport", icao_code="ESBB", country="Sweden")
        second = make_project("Bravo safety project", second_airport)
        document = Document(
            source=PublishingSource(name="Shared Publisher"),
            title="Shared document",
            status="active",
            projects=[first, second],
        )
        session.add(document)
        session.commit()
        document_id = document.id
        project_ids = (first.id, second.id)

    html = client.get(f"/documents/{document_id}").text

    for text in ("Alpha safety project", "AAA", "Alpha Airport", "Bana 01/19", "Bravo safety project", "ESBB", "Bravo Airport"):
        assert text in html
    for project_id in project_ids:
        assert f'href="/projects/{project_id}"' in html


def test_document_without_projects_shows_empty_state(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(name="Independent Publisher"),
            title="Unlinked document",
            status="active",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text
    assert "Inga projekt är kopplade till dokumentet." in html


def test_legacy_source_content_is_not_rendered(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        airport = Airport(name="Legacy Test Airport", country="Sweden")
        project = make_project("Normalized project", airport)
        project.sources.append(
            Source(
                title="LEGACY DETAIL MUST STAY HIDDEN",
                source_type="web",
                publisher="Legacy Publisher",
                url="https://legacy.example",
            )
        )
        document = Document(
            source=PublishingSource(name="Normalized Publisher"),
            title="Normalized document",
            status="active",
            projects=[project],
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text
    assert "Normalized Publisher" in html
    assert "LEGACY DETAIL MUST STAY HIDDEN" not in html
    assert "Legacy Publisher" not in html


def test_project_workspace_links_to_internal_document_detail(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        airport = Airport(name="Workspace Airport", country="Sweden")
        project = make_project("Workspace project", airport)
        document = Document(
            source=PublishingSource(name="Workspace Publisher"),
            title="Workspace linked document",
            status="active",
        )
        project.documents.append(document)
        session.add(project)
        session.commit()
        project_id = project.id
        document_id = document.id

    html = client.get(f"/projects/{project_id}").text
    assert f'href="/documents/{document_id}"' in html


def test_missing_document_returns_404_and_existing_routes_still_work(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        project = make_project(
            "Route smoke project",
            Airport(name="Route Test Airport", country="Sweden"),
        )
        session.add(project)
        session.commit()
        project_id = project.id
        airport_id = project.airport.id

    assert client.get("/documents/999999").status_code == 404
    assert client.get("/health").status_code == 200
    assert client.get("/airports").status_code == 200
    assert client.get(f"/airports/{airport_id}").status_code == 200
    assert client.get("/projects").status_code == 200
    assert client.get(f"/projects/{project_id}").status_code == 200
